// Read-only camera configuration: inspect actual modes, collect raw depth and timing.
#include <OpenNI.h>
#include <algorithm>
#include <chrono>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

static void check(openni::Status s, const char* operation) {
    if (s != openni::STATUS_OK)
        throw std::runtime_error(std::string(operation)+": "+openni::OpenNI::getExtendedError());
}

static void savePgm(const std::string& path, int width, int height,
                    const std::vector<uint16_t>& pixels) {
    std::ofstream pgm(path,std::ios::binary);
    pgm << "P5\n" << width << ' ' << height << "\n65535\n";
    for (auto z:pixels) { pgm.put(static_cast<char>(z>>8)); pgm.put(static_cast<char>(z&255)); }
    if (!pgm) throw std::runtime_error("Failed to save raw depth: "+path);
}

int main(int argc, char** argv) {
    if (argc != 3) { std::cerr << "Usage: depth_probe OUTPUT_DIRECTORY FRAME_COUNT\n"; return 2; }
    const std::string out = argv[1];
    const int count = std::stoi(argv[2]);
    if (count < 1 || count > 18000) return 2;
    openni::Device device;
    openni::VideoStream stream;
    bool initialized = false;
    int result = 0;
    try {
        if (std::ifstream(out+"/depth_frames.csv").good())
            throw std::runtime_error("Output already contains a capture; choose a fresh directory");
        std::ofstream csv(out+"/depth_frames.csv");
        std::ofstream metadata(out+"/depth_config.json");
        if (!csv || !metadata) throw std::runtime_error("Output directory must exist and be writable");
        csv << "frame_index,device_timestamp_us,host_elapsed_s,nonzero_pixels,in_range_pixels,min_nonzero_raw,max_raw,center_raw,roi_valid_pixels,roi_median_raw\n";
        check(openni::OpenNI::initialize(), "initialize");
        initialized = true;
        check(device.open(openni::ANY_DEVICE), "device.open");
        const auto* info = device.getSensorInfo(openni::SENSOR_DEPTH);
        if (!info) throw std::runtime_error("No depth sensor exposed by this device");
        const auto& modes = info->getSupportedVideoModes();
        std::cout << "Supported depth modes (width height fps pixel_format):\n";
        for (int i=0; i<modes.getSize(); ++i)
            std::cout << modes[i].getResolutionX() << ' ' << modes[i].getResolutionY()
                      << ' ' << modes[i].getFps() << ' ' << modes[i].getPixelFormat() << '\n';
        check(stream.create(device, openni::SENSOR_DEPTH), "stream.create");
        check(stream.start(), "stream.start");
        const auto mode = stream.getVideoMode();
        const int format = mode.getPixelFormat();
        const double scale = format == openni::PIXEL_FORMAT_DEPTH_1_MM ? 1.0 :
                             format == openni::PIXEL_FORMAT_DEPTH_100_UM ? 0.1 : 0.0;
        if (!scale) throw std::runtime_error("Not a supported metric depth format");
        const int minRaw = stream.getMinPixelValue(), maxRaw = stream.getMaxPixelValue();
        const int roiW=std::max(1,mode.getResolutionX()/10);
        const int roiH=std::max(1,mode.getResolutionY()/10);
        const int roiX=(mode.getResolutionX()-roiW)/2;
        const int roiY=(mode.getResolutionY()-roiH)/2;
        const auto version = openni::OpenNI::getVersion();
        metadata << "{\n  \"source\": \"REAL_CAMERA\",\n  \"sdk_version\": \""
                 << version.major << '.' << version.minor << '.' << version.maintenance << '.' << version.build
                 << "\",\n  \"width\": " << mode.getResolutionX()
                 << ",\n  \"height\": " << mode.getResolutionY()
                 << ",\n  \"nominal_fps\": " << mode.getFps()
                 << ",\n  \"pixel_format_enum\": " << format
                 << ",\n  \"millimeters_per_raw_unit\": " << scale
                 << ",\n  \"sdk_min_raw\": " << minRaw << ",\n  \"sdk_max_raw\": " << maxRaw
                 << ",\n  \"mirroring\": " << (stream.getMirroringEnabled()?"true":"false")
                 << ",\n  \"image_registration_mode\": " << device.getImageRegistrationMode()
                 << ",\n  \"raw_file\": \"depth_raw.pgm\",\n  \"raw_encoding\": \"P5 uint16 big-endian; no scaling or filtering\",\n"
                 << "  \"rgb_depth_alignment_verified\": false,\n"
                 << "  \"central_roi\": {\"x\": " << roiX << ", \"y\": " << roiY
                 << ", \"width\": " << roiW << ", \"height\": " << roiH
                 << ", \"verified_inside_target\": false},\n"
                 << "  \"roi_caution\": \"Fixed image ROI, not a segmented object. Median assumes most valid pixels lie on the same surface.\"\n}\n";
        metadata.close();
        const auto start = std::chrono::steady_clock::now();
        int received=0, timeouts=0;
        std::vector<uint16_t> pixels;
        int width=0, height=0;
        while (received < count) {
            auto* ptr=&stream;
            int changed;
            auto s=openni::OpenNI::waitForAnyStream(&ptr,1,&changed,2000);
            if (s!=openni::STATUS_OK) {
                if (++timeouts>=5) throw std::runtime_error("Five depth wait failures");
                continue;
            }
            openni::VideoFrameRef frame;
            check(stream.readFrame(&frame),"readFrame");
            if (!frame.isValid() || frame.getVideoMode().getPixelFormat()!=format)
                throw std::runtime_error("Invalid depth frame or changing format");
            width=frame.getWidth(); height=frame.getHeight();
            if (width!=mode.getResolutionX() || height!=mode.getResolutionY())
                throw std::runtime_error("Frame dimensions differ from configured mode");
            pixels.resize(width*height);
            int nonzero=0, inrange=0, lo=65535, hi=0;
            for (int y=0; y<height; ++y) {
                const auto* row=reinterpret_cast<const uint16_t*>(static_cast<const char*>(frame.getData())+y*frame.getStrideInBytes());
                for (int x=0; x<width; ++x) {
                    const uint16_t z=row[x]; pixels[y*width+x]=z;
                    if (z) { ++nonzero; lo=std::min(lo,int(z)); hi=std::max(hi,int(z)); }
                    if (z && z>=minRaw && z<=maxRaw) ++inrange;
                }
            }
            std::vector<uint16_t> roiValues;
            for (int y=roiY; y<roiY+roiH; ++y)
                for (int x=roiX; x<roiX+roiW; ++x) {
                    const auto z=pixels[y*width+x];
                    if (z && z>=minRaw && z<=maxRaw) roiValues.push_back(z);
                }
            std::sort(roiValues.begin(),roiValues.end());
            const auto n=roiValues.size();
            const double median=n ? (double(roiValues[(n-1)/2])+roiValues[n/2])/2.0 : 0.0;
            const auto center=pixels[(height/2)*width+width/2];
            double elapsed=std::chrono::duration<double>(std::chrono::steady_clock::now()-start).count();
            csv << frame.getFrameIndex() << ',' << frame.getTimestamp() << ',' << elapsed << ','
                << nonzero << ',' << inrange << ',' << (nonzero?lo:0) << ',' << hi << ','
                << center << ',' << n << ',';
            if (n) csv << median;
            csv << '\n';
            ++received;
            if (received==1) savePgm(out+"/depth_first.pgm",width,height,pixels);
            if (received==count/2) savePgm(out+"/depth_middle.pgm",width,height,pixels);
            if (received==1 || received%30==0)
                std::cout << "frame=" << received << " sensor_us=" << frame.getTimestamp()
                          << " nonzero=" << nonzero << '/' << width*height << " range_raw="
                          << (nonzero?lo:0) << ':' << hi << " center_raw=" << center
                          << " roi_valid=" << n << '/' << roiW*roiH << " roi_median_raw=";
            if (received==1 || received%30==0) {
                if (n) std::cout << median;
                else std::cout << "INVALID";
                std::cout << std::endl;
            }
        }
        savePgm(out+"/depth_raw.pgm",width,height,pixels);
        std::cout << "Saved unmodified uint16 depth. Wait failures=" << timeouts << std::endl;
    } catch (const std::exception& e) { std::cerr << e.what() << '\n'; result=1; }
    if (stream.isValid()) { stream.stop(); stream.destroy(); }
    if (device.isValid()) device.close();
    if (initialized) openni::OpenNI::shutdown();
    return result;
}
