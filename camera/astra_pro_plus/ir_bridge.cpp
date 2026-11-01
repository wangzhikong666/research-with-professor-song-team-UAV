// Minimal same-process bridge from the official OpenNI SDK to existing Python OpenCV.
#include <OpenNI.h>
#include <PS1080.h>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <string>

namespace {
openni::Device device;
openni::VideoStream ir;
openni::VideoStream depth;
bool initialized=false;
std::string error;
int fail(const char* step) {
    error=std::string(step)+": "+openni::OpenNI::getExtendedError();
    return -1;
}
}

extern "C" const char* astra_ir_error() { return error.c_str(); }

extern "C" void astra_ir_close() {
    if (depth.isValid()) { depth.stop(); depth.destroy(); }
    if (ir.isValid()) { ir.stop(); ir.destroy(); }
    if (device.isValid() && device.getImageRegistrationMode()!=openni::IMAGE_REGISTRATION_OFF)
        device.setImageRegistrationMode(openni::IMAGE_REGISTRATION_OFF);
    if (device.isValid()) device.close();
    if (initialized) { openni::OpenNI::shutdown(); initialized=false; }
}

extern "C" int astra_ir_open(int*,int*,int*,int*);

// Config: depth width,height,fps,format,mirror,min,max; IR width,height,fps,format,mirror; registration.
extern "C" int astra_scene_open(int* config,int capacity) {
    if (capacity<13) { error="Scene config buffer too small"; return -1; }
    int iw,ih,ifps,im;
    if (astra_ir_open(&iw,&ih,&ifps,&im)!=0) return -1;
    if (depth.create(device,openni::SENSOR_DEPTH)!=openni::STATUS_OK) {
        fail("depth.create"); astra_ir_close(); return -1;
    }
    if (depth.start()!=openni::STATUS_OK) {
        fail("depth.start with IR"); astra_ir_close(); return -1;
    }
    const auto mode=depth.getVideoMode();
    if (mode.getPixelFormat()!=openni::PIXEL_FORMAT_DEPTH_1_MM &&
        mode.getPixelFormat()!=openni::PIXEL_FORMAT_DEPTH_100_UM) {
        error="Depth stream is not a metric uint16 format"; astra_ir_close(); return -1;
    }
    config[0]=mode.getResolutionX(); config[1]=mode.getResolutionY();
    config[2]=mode.getFps(); config[3]=mode.getPixelFormat();
    config[4]=depth.getMirroringEnabled()?1:0;
    config[5]=depth.getMinPixelValue(); config[6]=depth.getMaxPixelValue();
    config[7]=iw; config[8]=ih; config[9]=ifps;
    config[10]=ir.getVideoMode().getPixelFormat(); config[11]=im;
    config[12]=device.getImageRegistrationMode();
    return 0;
}

static int openDepth(int* config,int capacity,bool unmirrored,bool registered=false) {
    if (capacity<13) { error="Depth config buffer too small"; return -1; }
    error.clear();
    if (initialized) { error="Device is already open"; return -1; }
    if (openni::OpenNI::initialize()!=openni::STATUS_OK) return fail("initialize");
    initialized=true;
    if (device.open(openni::ANY_DEVICE)!=openni::STATUS_OK) {
        fail("device.open"); astra_ir_close(); return -1;
    }
    if (depth.create(device,openni::SENSOR_DEPTH)!=openni::STATUS_OK) {
        fail("depth.create"); astra_ir_close(); return -1;
    }
    if (unmirrored && depth.setMirroringEnabled(false)!=openni::STATUS_OK) {
        fail("disable mirroring"); astra_ir_close(); return -1;
    }
    const auto registration=registered?openni::IMAGE_REGISTRATION_DEPTH_TO_COLOR:openni::IMAGE_REGISTRATION_OFF;
    if (device.setImageRegistrationMode(registration)!=openni::STATUS_OK) {
        fail("set requested registration mode"); astra_ir_close(); return -1;
    }
    if (depth.start()!=openni::STATUS_OK) {
        fail("depth.start"); astra_ir_close(); return -1;
    }
    const auto mode=depth.getVideoMode();
    if (mode.getPixelFormat()!=openni::PIXEL_FORMAT_DEPTH_1_MM &&
        mode.getPixelFormat()!=openni::PIXEL_FORMAT_DEPTH_100_UM) {
        error="Depth stream is not metric uint16"; astra_ir_close(); return -1;
    }
    config[0]=mode.getResolutionX(); config[1]=mode.getResolutionY();
    config[2]=mode.getFps(); config[3]=mode.getPixelFormat();
    config[4]=depth.getMirroringEnabled()?1:0;
    config[5]=depth.getMinPixelValue(); config[6]=depth.getMaxPixelValue();
    config[7]=config[0]; config[8]=config[1];
    config[9]=0; config[10]=0; config[11]=0; // No IR stream is created.
    config[12]=device.getImageRegistrationMode();
    return 0;
}

extern "C" int astra_depth_open(int* config,int capacity) {
    return openDepth(config,capacity,false);
}
extern "C" int astra_point_open(int* config,int capacity) {
    return openDepth(config,capacity,true);
}
extern "C" int astra_registered_open(int* config,int capacity) {
    return openDepth(config,capacity,true,true);
}

extern "C" int astra_depth_calibration(float* values,int capacity) {
    if (!depth.isValid() || capacity<30) { error="Invalid calibration request"; return -1; }
    OBCameraParams params={};
    static_assert(sizeof(params)==30*sizeof(float),"Unexpected calibration ABI");
    int size=sizeof(params);
    if (device.getProperty(openni::OBEXTENSION_ID_CAM_PARAMS,
                           reinterpret_cast<uint8_t*>(&params),&size)!=openni::STATUS_OK)
        return fail("read factory calibration");
    if (size!=sizeof(params)) { error="Unexpected calibration payload size"; return -1; }
    std::memcpy(values,&params,sizeof(params));
    return 0;
}
extern "C" int astra_depth_distortion_state(uint64_t* value) {
    if (!depth.isValid()) { error="Depth is not open"; return -1; }
    *value=0;
    return device.getProperty(XN_MODULE_PROPERTY_DISTORTION_STATE,value);
}
// Read-only capability and pixel-registration queries. Never enable hardware D2C here.
extern "C" int astra_depth_registration_supported() {
    if (!depth.isValid()) return -1;
    return device.isImageRegistrationModeSupported(openni::IMAGE_REGISTRATION_DEPTH_TO_COLOR)?1:0;
}
extern "C" int astra_depth_registration_query(int mode,int u,int v,uint16_t raw,
                                               int colorWidth,int colorHeight,int* output) {
    if (!depth.isValid() || !output || mode<0 || mode>1 || u<0 || v<0 || !raw ||
        colorWidth<=0 || colorHeight<=0) return -1;
    const auto videoMode=depth.getVideoMode();
    if (u>=videoMode.getResolutionX() || v>=videoMode.getResolutionY() ||
        raw>depth.getMaxPixelValue() || device.getImageRegistrationMode()!=openni::IMAGE_REGISTRATION_OFF) {
        error="Pixel registration query requires an in-range original unregistered depth pixel";
        return -1;
    }
    XnPixelRegistration pixel={};
    pixel.nDepthX=u; pixel.nDepthY=v; pixel.nDepthValue=raw;
    pixel.nImageXRes=colorWidth; pixel.nImageYRes=colorHeight;
    int size=sizeof(pixel);
    const int property=mode?XN_STREAM_PROPERTY_PIXEL_D2C_REGISTRATION:XN_STREAM_PROPERTY_PIXEL_REGISTRATION;
    const auto status=depth.getProperty(property,reinterpret_cast<uint8_t*>(&pixel),&size);
    output[0]=pixel.nImageX; output[1]=pixel.nImageY; output[2]=size;
    return status;
}
extern "C" int astra_depth_to_world(float u,float v,float raw,float* xyz) {
    if (!depth.isValid()) { error="Depth is not open"; return -1; }
    const auto mode=depth.getVideoMode();
    if (!std::isfinite(u)||!std::isfinite(v)||!std::isfinite(raw)||raw<=0 ||
        u<0||v<0||u>=mode.getResolutionX()||v>=mode.getResolutionY()) {
        error="Invalid pixel or raw depth"; return -1;
    }
    if (openni::CoordinateConverter::convertDepthToWorld(depth,u,v,raw,xyz,xyz+1,xyz+2)
            !=openni::STATUS_OK) return fail("convertDepthToWorld");
    if (!std::isfinite(xyz[0])||!std::isfinite(xyz[1])||!std::isfinite(xyz[2])) {
        error="Non-finite SDK coordinates"; return -1;
    }
    return 0;
}
extern "C" int astra_world_to_depth(float x,float y,float z,float* pixel) {
    if (!depth.isValid()) { error="Depth is not open"; return -1; }
    if (openni::CoordinateConverter::convertWorldToDepth(depth,x,y,z,pixel,pixel+1,pixel+2)
            !=openni::STATUS_OK) return fail("convertWorldToDepth");
    return 0;
}

// kind=0: new depth frame; kind=1: new IR frame, if enabled. No sync claim.
extern "C" int astra_scene_read(int* kind,void* output,int capacity,uint64_t* timestamp,int* index) {
    if (!depth.isValid()) { error="Depth is not open"; return -1; }
    openni::VideoStream* streams[]={&depth,&ir}; int changed;
    const int streamCount=ir.isValid()?2:1;
    const auto status=openni::OpenNI::waitForAnyStream(streams,streamCount,&changed,100);
    if (status==openni::STATUS_TIME_OUT) return 1;
    if (status!=openni::STATUS_OK) return fail("wait scene");
    if (changed<0 || changed>=streamCount) { error="Invalid stream index"; return -1; }
    openni::VideoFrameRef frame;
    if (streams[changed]->readFrame(&frame)!=openni::STATUS_OK) return fail("read scene");
    const auto mode=streams[changed]->getVideoMode();
    if (!frame.isValid() || frame.getVideoMode().getPixelFormat()!=mode.getPixelFormat() ||
        frame.getWidth()!=mode.getResolutionX() || frame.getHeight()!=mode.getResolutionY()) {
        error="Unexpected scene frame format or dimensions"; return -1;
    }
    const int rowBytes=frame.getWidth()*int(sizeof(uint16_t));
    if (capacity<rowBytes*frame.getHeight()) { error="Scene output buffer too small"; return -1; }
    for (int y=0;y<frame.getHeight();++y)
        std::memcpy(static_cast<char*>(output)+y*rowBytes,
                    static_cast<const char*>(frame.getData())+y*frame.getStrideInBytes(),rowBytes);
    *kind=changed; *timestamp=frame.getTimestamp(); *index=frame.getFrameIndex();
    return 0;
}

extern "C" int astra_ir_open(int* width,int* height,int* fps,int* mirror) {
    error.clear();
    if (initialized) { error="IR is already open"; return -1; }
    if (openni::OpenNI::initialize()!=openni::STATUS_OK) return fail("initialize");
    initialized=true;
    if (device.open(openni::ANY_DEVICE)!=openni::STATUS_OK) { fail("device.open"); astra_ir_close(); return -1; }
    const auto* info=device.getSensorInfo(openni::SENSOR_IR);
    if (!info) { error="IR sensor not exposed"; astra_ir_close(); return -1; }
    if (ir.create(device,openni::SENSOR_IR)!=openni::STATUS_OK) { fail("ir.create"); astra_ir_close(); return -1; }
    const auto& modes=info->getSupportedVideoModes();
    bool selected=false;
    for (int i=0;i<modes.getSize();++i)
        if (modes[i].getResolutionX()==640 && modes[i].getResolutionY()==480 &&
            modes[i].getFps()==30 && modes[i].getPixelFormat()==openni::PIXEL_FORMAT_GRAY16) {
            if (ir.setVideoMode(modes[i])!=openni::STATUS_OK) { fail("ir.setVideoMode"); astra_ir_close(); return -1; }
            selected=true; break;
        }
    if (!selected) { error="640x480 GRAY16 30 FPS not exposed"; astra_ir_close(); return -1; }
    if (ir.start()!=openni::STATUS_OK) { fail("ir.start"); astra_ir_close(); return -1; }
    const auto mode=ir.getVideoMode();
    *width=mode.getResolutionX(); *height=mode.getResolutionY();
    *fps=mode.getFps(); *mirror=ir.getMirroringEnabled()?1:0;
    return 0;
}

extern "C" int astra_ir_read(void* output,int capacity,uint64_t* timestamp) {
    if (!ir.isValid()) { error="IR is not open"; return -1; }
    auto* stream=&ir; int changed;
    auto status=openni::OpenNI::waitForAnyStream(&stream,1,&changed,500);
    if (status==openni::STATUS_TIME_OUT) return 1;
    if (status!=openni::STATUS_OK) return fail("wait IR");
    openni::VideoFrameRef frame;
    if (ir.readFrame(&frame)!=openni::STATUS_OK) return fail("read IR");
    if (!frame.isValid() || frame.getVideoMode().getPixelFormat()!=openni::PIXEL_FORMAT_GRAY16) {
        error="Invalid GRAY16 IR frame"; return -1;
    }
    const int rowBytes=frame.getWidth()*int(sizeof(uint16_t));
    if (capacity<rowBytes*frame.getHeight()) { error="IR output buffer too small"; return -1; }
    for (int y=0;y<frame.getHeight();++y)
        std::memcpy(static_cast<char*>(output)+y*rowBytes,
                    static_cast<const char*>(frame.getData())+y*frame.getStrideInBytes(),rowBytes);
    *timestamp=frame.getTimestamp();
    return 0;
}
