"""Reject missing depth, range failures, sparse support and depth discontinuities."""
import numpy as np


def assess_pixel(raw, u, v, scale, minimum_mm, maximum_mm, sdk_min, sdk_max):
    height, width = raw.shape
    result = {'valid':False,'pixel_uv':[int(u),int(v)],'position_m':None}
    if not (0 <= u < width and 0 <= v < height):
        return dict(result,reason='OUTSIDE_IMAGE')
    value = int(raw[v,u])
    result.update(raw_depth=value,depth_mm=value*scale if value else None)
    if value == 0:
        return dict(result,reason='NO_DEPTH')
    if not sdk_min <= value <= sdk_max:
        return dict(result,reason='SDK_RANGE')
    if not minimum_mm <= value*scale <= maximum_mm:
        return dict(result,reason='APPLICATION_RANGE')
    if u < 1 or v < 1 or u >= width-1 or v >= height-1:
        return dict(result,reason='IMAGE_BORDER')
    patch = raw[v-1:v+2,u-1:u+2]
    valid = ((patch>0)&(patch>=sdk_min)&(patch<=sdk_max)&
             (patch*scale>=minimum_mm)&(patch*scale<=maximum_mm))
    values = patch[valid].astype(float)*scale
    result['quality'] = {
        'neighborhood':'3x3','valid_neighbors':int(values.size),'total_neighbors':9,
        'minimum_valid_neighbors':7,
        'depth_spread_mm':float(np.ptp(values)) if values.size else None,
        'maximum_allowed_spread_mm':max(10.0,0.02*value*scale),
        'depth_selection':'Exact clicked pixel; no median substitution or hole filling',
    }
    if values.size < 7:
        return dict(result,reason='SPARSE_NEIGHBORHOOD')
    if np.ptp(values) > result['quality']['maximum_allowed_spread_mm']:
        return dict(result,reason='DEPTH_DISCONTINUITY')
    return dict(result,valid=True,reason='DEPTH_QUALITY_PASSED')


def self_check():
    # Offline algorithm tests, NOT camera measurements.
    a = np.full((9,9),900,np.uint16)
    args = (1.0,600,4000,0,10000)
    assert assess_pixel(a,4,4,*args)['valid']
    assert assess_pixel(a,-1,4,*args)['reason']=='OUTSIDE_IMAGE'
    assert assess_pixel(a,0,4,*args)['reason']=='IMAGE_BORDER'
    a[4,4]=0
    assert assess_pixel(a,4,4,*args)['reason']=='NO_DEPTH'
    a[4,4]=65535
    assert assess_pixel(a,4,4,*args)['reason']=='SDK_RANGE'
    a[4,4]=500
    assert assess_pixel(a,4,4,*args)['reason']=='APPLICATION_RANGE'
    a[4,4]=900
    a[3:6,3]=0
    assert assess_pixel(a,4,4,*args)['reason']=='SPARSE_NEIGHBORHOOD'
    a[:]=900
    a[3:6,5]=1200
    assert assess_pixel(a,4,4,*args)['reason']=='DEPTH_DISCONTINUITY'
    return '8 offline depth-quality checks passed; not hardware verification'
