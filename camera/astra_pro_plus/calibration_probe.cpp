// Read actual factory parameters and test the active SDK coordinate conversion.
#include <OpenNI.h>
#include <PS1080.h>
#include <cmath>
#include <cstdint>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <stdexcept>
#include <string>

static void check(openni::Status status,const char* step) {
    if (status!=openni::STATUS_OK)
        throw std::runtime_error(std::string(step)+": "+openni::OpenNI::getExtendedError());
}
static void array(std::ostream& out,const float* data,int count) {
    out << '[';
    for (int i=0;i<count;++i) {
        if (i) out << ',';
        if (std::isfinite(data[i])) out << data[i]; else out << "null";
    }
    out << ']';
}

int main(int argc,char** argv) {
    if (argc!=2) { std::cerr << "Usage: calibration_probe OUTPUT_DIRECTORY\n"; return 2; }
    const std::string folder=argv[1];
    openni::Device device; openni::VideoStream stream;
    bool initialized=false; int result=0;
    try {
        if (std::ifstream(folder+"/calibration.json").good())
            throw std::runtime_error("Calibration output exists; choose fresh directory");
        check(openni::OpenNI::initialize(),"initialize"); initialized=true;
        check(device.open(openni::ANY_DEVICE),"device.open");
        check(stream.create(device,openni::SENSOR_DEPTH),"depth.create");
        check(stream.setMirroringEnabled(false),"disable depth mirroring");
        check(stream.start(),"depth.start");
        const auto mode=stream.getVideoMode();
        if (stream.getMirroringEnabled()) throw std::runtime_error("Mirroring did not turn off");
        if (mode.getPixelFormat()!=openni::PIXEL_FORMAT_DEPTH_1_MM)
            throw std::runtime_error("This probe requires verified DEPTH_1_MM format");
        OBCameraParams params={};
        int size=sizeof(params);
        auto parameterStatus=device.getProperty(openni::OBEXTENSION_ID_CAM_PARAMS,
                                                reinterpret_cast<uint8_t*>(&params),&size);
        uint64_t distortion=0;
        auto distortionStatus=device.getProperty(XN_MODULE_PROPERTY_DISTORTION_STATE,&distortion);
        float hfov=0,vfov=0;
        auto hfovStatus=stream.getProperty(openni::STREAM_PROPERTY_HORIZONTAL_FOV,&hfov);
        auto vfovStatus=stream.getProperty(openni::STREAM_PROPERTY_VERTICAL_FOV,&vfov);
        std::ofstream out(folder+"/calibration.json");
        out << std::setprecision(10);
        out << "{\n\"source\":\"REAL_CAMERA\",\n\"width\":" << mode.getResolutionX()
            << ",\n\"height\":" << mode.getResolutionY() << ",\n\"nominal_fps\":" << mode.getFps()
            << ",\n\"pixel_format_enum\":" << mode.getPixelFormat()
            << ",\n\"millimeters_per_raw_unit\":1,\n\"mirroring\":false,"
            << "\n\"registration_mode\":" << device.getImageRegistrationMode()
            << ",\n\"factory_query_status\":" << parameterStatus
            << ",\n\"factory_returned_bytes\":" << size
            << ",\n\"factory_expected_bytes\":" << sizeof(params)
            << ",\n\"factory_depth_intrinsics_fx_fy_cx_cy\":";
        array(out,params.l_intr_p,4);
        out << ",\n\"factory_color_intrinsics_fx_fy_cx_cy\":"; array(out,params.r_intr_p,4);
        out << ",\n\"factory_depth_distortion_k1_k2_p1_p2_k3\":"; array(out,params.l_k,5);
        out << ",\n\"factory_color_distortion_k1_k2_p1_p2_k3\":"; array(out,params.r_k,5);
        out << ",\n\"factory_r2l_rotation_uninterpreted\":"; array(out,params.r2l_r,9);
        out << ",\n\"factory_r2l_translation_uninterpreted\":"; array(out,params.r2l_t,3);
        out << ",\n\"distortion_state_query_status\":" << distortionStatus
            << ",\n\"distortion_state_raw\":" << distortion
            << ",\n\"horizontal_fov_query_status\":" << hfovStatus
            << ",\n\"horizontal_fov_radians\":" << hfov
            << ",\n\"vertical_fov_query_status\":" << vfovStatus
            << ",\n\"vertical_fov_radians\":" << vfov
            << ",\n\"synthetic_projection_tests\":[\n";
        const float w=mode.getResolutionX(),h=mode.getResolutionY();
        const float points[][2]={{w/2,h/2},{w/2+1,h/2},{w/2,h/2+1},
                                 {100,h/2},{w-101,h/2},{w/2,100},{w/2,h-101},
                                 {0,0},{w-1,h-1},{params.l_intr_p[2],params.l_intr_p[3]}};
        for (int i=0;i<10;++i) {
            float x=0,y=0,z=0;
            auto status=openni::CoordinateConverter::convertDepthToWorld(
                stream,points[i][0],points[i][1],1000.0f,&x,&y,&z);
            if (i) out << ",\n";
            out << "{\"u\":" << points[i][0] << ",\"v\":" << points[i][1]
                << ",\"input_z_mm\":1000,\"status\":" << status << ",\"sdk_xyz_mm\":";
            const float xyz[]={x,y,z}; array(out,xyz,3); out << '}';
        }
        out << "],\n\"projection_test_note\":\"Synthetic input Z=1000 mm tests SDK geometry; not a physical measurement.\",\n"
            << "\"actual_samples\":[\n";
        for (int i=0;i<30;++i) {
            auto* ptr=&stream; int changed; openni::VideoFrameRef frame;
            check(openni::OpenNI::waitForAnyStream(&ptr,1,&changed,2000),"wait depth");
            check(stream.readFrame(&frame),"read depth");
            int u=frame.getWidth()/2,v=frame.getHeight()/2;
            const auto* row=reinterpret_cast<const uint16_t*>(
                static_cast<const char*>(frame.getData())+v*frame.getStrideInBytes());
            uint16_t raw=row[u]; float x=0,y=0,z=0;
            auto status=raw?openni::CoordinateConverter::convertDepthToWorld(stream,u,v,raw,&x,&y,&z)
                           :openni::STATUS_BAD_PARAMETER;
            if (i) out << ",\n";
            out << "{\"u\":" << u << ",\"v\":" << v << ",\"raw_z\":" << raw
                << ",\"timestamp_us\":" << frame.getTimestamp() << ",\"status\":" << status
                << ",\"sdk_xyz_mm\":";
            const float xyz[]={x,y,z}; array(out,xyz,3); out << '}';
        }
        out << "],\n\"camera_motion_sent\":false\n}\n";
        if (!out) throw std::runtime_error("Cannot save calibration report");
        std::cout << "Saved actual parameters and SDK geometry tests; factory status="
                  << parameterStatus << ", distortion state status=" << distortionStatus << '\n';
    } catch (const std::exception& error) { std::cerr << error.what() << '\n'; result=1; }
    if (stream.isValid()) { stream.stop(); stream.destroy(); }
    if (device.isValid()) device.close();
    if (initialized) openni::OpenNI::shutdown();
    return result;
}
