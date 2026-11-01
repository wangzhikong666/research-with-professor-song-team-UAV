// A separate IR exposure for scene inspection. Not a synchronized RGB-D pair.
#include <OpenNI.h>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>

static void check(openni::Status status, const char* operation) {
    if (status!=openni::STATUS_OK)
        throw std::runtime_error(std::string(operation)+": "+openni::OpenNI::getExtendedError());
}

int main(int argc, char** argv) {
    if (argc!=2) { std::cerr << "Usage: ir_snapshot OUTPUT_DIRECTORY\n"; return 2; }
    openni::Device device;
    openni::VideoStream ir;
    bool initialized=false;
    int result=0;
    const std::string out=argv[1];
    try {
        if (std::ifstream(out+"/ir_raw.pgm").good())
            throw std::runtime_error("IR snapshot already exists; use a fresh directory");
        check(openni::OpenNI::initialize(),"initialize"); initialized=true;
        check(device.open(openni::ANY_DEVICE),"device.open");
        const auto* info=device.getSensorInfo(openni::SENSOR_IR);
        if (!info) throw std::runtime_error("No IR sensor exposed");
        check(ir.create(device,openni::SENSOR_IR),"ir.create");
        const auto& modes=info->getSupportedVideoModes();
        bool selected=false;
        for (int i=0;i<modes.getSize();++i)
            if (modes[i].getResolutionX()==640 && modes[i].getResolutionY()==480 &&
                modes[i].getFps()==30 && modes[i].getPixelFormat()==openni::PIXEL_FORMAT_GRAY16) {
                check(ir.setVideoMode(modes[i]),"ir.setVideoMode"); selected=true; break;
            }
        if (!selected) throw std::runtime_error("640x480 GRAY16 30 FPS not exposed");
        check(ir.start(),"ir.start");
        openni::VideoFrameRef frame;
        for (int i=0;i<30;++i) {
            auto* ptr=&ir; int changed;
            check(openni::OpenNI::waitForAnyStream(&ptr,1,&changed,2000),"wait IR");
            check(ir.readFrame(&frame),"read IR");
        }
        if (!frame.isValid() || frame.getVideoMode().getPixelFormat()!=openni::PIXEL_FORMAT_GRAY16)
            throw std::runtime_error("IR frame is not valid GRAY16");
        const int width=frame.getWidth(), height=frame.getHeight();
        std::ofstream raw(out+"/ir_raw.pgm",std::ios::binary);
        raw << "P5\n" << width << ' ' << height << "\n65535\n";
        for (int y=0;y<height;++y) {
            const auto* row=reinterpret_cast<const openni::Grayscale16Pixel*>(
                static_cast<const char*>(frame.getData())+y*frame.getStrideInBytes());
            for (int x=0;x<width;++x) { raw.put(static_cast<char>(row[x]>>8)); raw.put(static_cast<char>(row[x]&255)); }
        }
        if (!raw) throw std::runtime_error("Cannot save IR raw file");
        int exposure=0,gain=0,autoExposure=0;
        const auto exposureStatus=ir.getProperty(openni::STREAM_PROPERTY_EXPOSURE,&exposure);
        const auto gainStatus=ir.getProperty(openni::STREAM_PROPERTY_GAIN,&gain);
        const auto autoStatus=ir.getProperty(openni::STREAM_PROPERTY_AUTO_EXPOSURE,&autoExposure);
        const auto mode=ir.getVideoMode();
        std::ofstream metadata(out+"/ir_config.json");
        metadata << "{\n  \"source\": \"REAL_CAMERA\",\n  \"width\": " << width
                 << ",\n  \"height\": " << height << ",\n  \"nominal_fps\": " << mode.getFps()
                 << ",\n  \"pixel_format_enum\": " << mode.getPixelFormat()
                 << ",\n  \"mirroring\": " << (ir.getMirroringEnabled()?"true":"false")
                 << ",\n  \"device_timestamp_us\": " << frame.getTimestamp()
                 << ",\n  \"units\": \"IR intensity device counts; NOT distance\",\n"
                 << "  \"exposure_query\": {\"status\": " << exposureStatus << ", \"value\": " << exposure << "},\n"
                 << "  \"gain_query\": {\"status\": " << gainStatus << ", \"value\": " << gain << "},\n"
                 << "  \"auto_exposure_query\": {\"status\": " << autoStatus << ", \"value\": " << autoExposure << "},\n"
                 << "  \"controls_note\": \"Read only. A nonzero status means unsupported/failed; control units not verified.\",\n"
                 << "  \"synchronized_with_depth\": false,\n"
                 << "  \"note\": \"Separate IR run after depth capture; no alignment claim\"\n}\n";
        if (!metadata) throw std::runtime_error("Cannot save IR metadata");
        std::cout << "Saved real IR snapshot " << width << 'x' << height << "; not depth-synchronized\n";
    } catch (const std::exception& error) { std::cerr << error.what() << '\n'; result=1; }
    if (ir.isValid()) { ir.stop(); ir.destroy(); }
    if (device.isValid()) device.close();
    if (initialized) openni::OpenNI::shutdown();
    return result;
}
