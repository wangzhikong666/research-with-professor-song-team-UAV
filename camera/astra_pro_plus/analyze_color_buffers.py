"""Inspect saved real UVC buffers; decoding is not proof of usable live RGB."""
import argparse
import hashlib
import json
from pathlib import Path
import cv2
import numpy as np


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path)
    parser.add_argument('--format',choices=('MJPG','YUYV'),default='MJPG')
    parser.add_argument('--width',type=int,default=320)
    parser.add_argument('--height',type=int,default=240)
    args=parser.parse_args()
    suffix='jpg' if args.format=='MJPG' else 'yuyv'
    records=[]; saved=0
    for file in sorted(args.directory.glob('frame-*.'+suffix)):
        data=file.read_bytes(); image=None
        if args.format=='MJPG' and data:
            image=cv2.imdecode(np.frombuffer(data,dtype=np.uint8),cv2.IMREAD_COLOR)
        elif args.format=='YUYV' and len(data)==args.width*args.height*2:
            image=cv2.cvtColor(np.frombuffer(data,dtype=np.uint8).reshape(args.height,args.width,2),cv2.COLOR_YUV2BGR_YUY2)
        record={'file':file.name,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),
                'all_zero':bool(data) and not any(data),
                'decoded_shape':list(image.shape) if image is not None else None}
        if args.format=='MJPG':
            record.update(jpeg_soi_count=data.count(b'\xff\xd8'),jpeg_eoi_count=data.count(b'\xff\xd9'))
        if image is not None and saved<3:
            cv2.imwrite(str(file.with_suffix('.png')),image); saved+=1
        records.append(record)
    decoded=sum(row['decoded_shape'] is not None for row in records)
    report={'source':'REAL_CAMERA_SAVED_BUFFER_ANALYSIS','requested_format':args.format,
            'requested_size':[args.width,args.height],'buffer_count':len(records),
            'decodable_buffer_count':decoded,'all_zero_buffer_count':sum(r['all_zero'] for r in records),
            'unique_buffer_hash_count':len({r['sha256'] for r in records}),
            'acceptance':'FAILED_NO_DECODABLE_BUFFERS' if not decoded else 'DECODED_FRAMES_REQUIRE_VISUAL_AND_LIVE_VERIFICATION',
            'rgb_depth_alignment_verified':False,'buffers':records}
    (args.directory/'analysis.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='buffers'},indent=2))


if __name__=='__main__':
    main()
