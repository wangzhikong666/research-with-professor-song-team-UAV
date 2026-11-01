// Query the OpenNI device without starting any stream or changing settings.
#include <OpenNI.h>
#include <iostream>

int main() {
    auto status = openni::OpenNI::initialize();
    if (status != openni::STATUS_OK) {
        std::cerr << openni::OpenNI::getExtendedError() << '\n';
        return 1;
    }
    openni::Device device;
    status = device.open(openni::ANY_DEVICE);
    if (status != openni::STATUS_OK) {
        std::cerr << openni::OpenNI::getExtendedError() << '\n';
        openni::OpenNI::shutdown();
        return 1;
    }
    const auto& info = device.getDeviceInfo();
    std::cout << "OpenNI device: " << info.getVendor() << " / " << info.getName() << '\n';
    const openni::SensorType types[] = {openni::SENSOR_DEPTH, openni::SENSOR_IR, openni::SENSOR_COLOR};
    const char* names[] = {"DEPTH", "IR", "COLOR"};
    for (int i=0; i<3; ++i) {
        const auto* sensor = device.getSensorInfo(types[i]);
        std::cout << names[i] << ": " << (sensor ? "exposed" : "not exposed") << '\n';
        if (!sensor) continue;
        const auto& modes = sensor->getSupportedVideoModes();
        for (int j=0; j<modes.getSize(); ++j)
            std::cout << "  " << modes[j].getResolutionX() << 'x' << modes[j].getResolutionY()
                      << " fps=" << modes[j].getFps()
                      << " pixel_format=" << modes[j].getPixelFormat() << '\n';
    }
    device.close();
    openni::OpenNI::shutdown();
}
