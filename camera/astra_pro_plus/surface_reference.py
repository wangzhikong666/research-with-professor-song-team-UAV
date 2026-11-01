"""A fixed-camera surface reference; store selection constraints, never held XYZ."""
import numpy as np
from point_quality import assess_pixel
from rgb_roi_depth import in_box


class SurfaceReference:
    rgb_radius_px=12
    depth_radius_px=4

    def __init__(self):
        self.reset()

    def reset(self,anchor=None):
        self.anchor=None if anchor is None else list(map(int,anchor))
        self.reference=None
        self.confirmed=False
        self.pending=[]

    def metadata(self):
        return {'anchor_rgb_uv':self.anchor,'anchor_search_radius_rgb_px':self.rgb_radius_px,
                'reference_depth_pixel_uv':None if self.reference is None else self.reference['depth_pixel_uv'],
                'reference_initial_depth_mm':None if self.reference is None else self.reference['raw_depth'],
                'depth_band_half_width_mm':None if self.reference is None else self.reference['band_mm'],
                'maximum_neighbor_radius_depth_px':self.depth_radius_px,
                'initial_reference_confirmed':self.confirmed,
                'assumption':'Fixed camera and target; nearby accepted samples assumed to be the same surface',
                'identity_tracking_verified':False}

    def candidates(self,raw,box,projection,sdk_min=0,sdk_max=10000):
        mask=np.zeros(raw.shape,np.uint8)
        invalid={'valid':False,'position_m':None,'candidate_points':[]}
        if box is None:return dict(invalid,reason='RGB_BOX_REQUIRED'),mask
        x,y,w,h=map(int,box)
        if x<0 or y<0 or x+w>640 or y+h>480 or min(w,h)<24:
            return dict(invalid,reason='RGB_BOX_TOO_SMALL_OR_OUTSIDE_IMAGE'),mask
        if self.anchor is None:return dict(invalid,reason='REFERENCE_CLICK_REQUIRED'),mask
        if not in_box(self.anchor,box):return dict(invalid,reason='REFERENCE_CLICK_TOO_CLOSE_TO_BOX_EDGE'),mask
        # Include out-of-application-range nonzero samples in the occlusion test.
        # They can hide an acceptable surface but cannot themselves become XYZ.
        v,u=np.nonzero((raw>0)&(raw>=sdk_min)&(raw<=sdk_max))
        if not len(u):return dict(invalid,reason='REFERENCE_LOCAL_DEPTH_MISSING'),mask
        uv,zcolor=projection.pixels(u,v,raw[v,u].astype(float))
        finite=np.isfinite(uv).all(axis=1)&np.isfinite(zcolor)&(zcolor>0)
        u,v,uv,zcolor=u[finite],v[finite],uv[finite],zcolor[finite]
        uv=np.floor(uv).astype(np.int32)
        inside=(uv[:,0]>=0)&(uv[:,0]<640)&(uv[:,1]>=0)&(uv[:,1]<480)
        u,v,uv,zcolor=u[inside],v[inside],uv[inside],zcolor[inside]
        keys=uv[:,1]*640+uv[:,0]; front=np.full(640*480,np.inf)
        np.minimum.at(front,keys,zcolor)
        visible=zcolor<=front[keys]+5
        anchor_distance=np.sum((uv-np.asarray(self.anchor))**2,axis=1)
        take=visible&(anchor_distance<=self.rgb_radius_px**2)&(raw[v,u]>=600)&(raw[v,u]<=4000)
        take&=(uv[:,0]>=x+6)&(uv[:,0]<x+w-6)&(uv[:,1]>=y+6)&(uv[:,1]<y+h-6)
        if self.reference is not None:
            ru,rv=self.reference['depth_pixel_uv']
            source_distance=(u-ru)**2+(v-rv)**2
            take&=source_distance<=self.depth_radius_px**2
            if np.any(take) and not np.any(take&(
                    np.abs(raw[v,u].astype(float)-self.reference['raw_depth'])<=self.reference['band_mm'])):
                return dict(invalid,reason='REFERENCE_DEPTH_CHANGED_RESELECT'),mask
            take&=np.abs(raw[v,u].astype(float)-self.reference['raw_depth'])<=self.reference['band_mm']
        else:source_distance=anchor_distance
        u,v,uv=u[take],v[take],uv[take]
        order=np.lexsort((anchor_distance[take],source_distance[take]))
        points=[]
        for index in order:
            du,dv=int(u[index]),int(v[index]); value=int(raw[dv,du])
            quality=assess_pixel(raw,du,dv,1,600,4000,sdk_min,sdk_max)
            if not quality['valid']:continue
            mask[dv,du]=255
            points.append({'depth_pixel_uv':[du,dv],'raw_depth':value,
                           'predicted_rgb_uv':uv[index].tolist(),'quality':quality['quality'],
                           'anchor_distance_rgb_px':float(np.linalg.norm(uv[index]-self.anchor))})
            if len(points)>=25:break
        if not points:return dict(invalid,reason='REFERENCE_LOCAL_DEPTH_MISSING'),mask
        if self.reference is None:
            # A click on a boundary can be equally close to two different layers.
            nearest=points[0]
            competing=[p for p in points[1:] if
                       p['anchor_distance_rgb_px']<=nearest['anchor_distance_rgb_px']+2 and
                       abs(p['raw_depth']-nearest['raw_depth'])>max(30,.03*nearest['raw_depth'])]
            if competing:return dict(invalid,reason='REFERENCE_CLICK_ON_DEPTH_BOUNDARY'),mask
        return dict(invalid,valid=True,reason='REFERENCE_CANDIDATES_REQUIRE_SDK_CHECK',
                    candidate_points=points,surface_selection='Fixed reference pixel with bounded same-surface neighbors'),mask

    def bind(self,point,sdk_rgb_uv):
        """Call only after a CURRENT original-depth sample passes native SDK D2C."""
        if self.reference is None:
            self.reference={'depth_pixel_uv':list(point['depth_pixel_uv']),
                            'raw_depth':int(point['raw_depth']),
                            'native_sdk_rgb_uv':list(sdk_rgb_uv),
                            'band_mm':max(30.,.03*point['raw_depth'])}

    def accept(self,frame_index,now):
        if self.reference is None:raise RuntimeError('Native-checked reference must be bound first')
        if self.confirmed:return True
        if not self.pending or self.pending[-1][0]!=frame_index:
            self.pending.append((frame_index,now))
        if len(self.pending)>=3 and now-self.pending[0][1]>=.25:
            self.confirmed=True
        return self.confirmed

    def reject(self):
        # Once confirmed, each CURRENT sample is independently checked. A missing
        # frame returns null; it does not destroy the static selection constraints.
        if not self.confirmed:self.pending=[]

    def sample_offset(self,point):
        return float(np.linalg.norm(np.asarray(point['depth_pixel_uv'])-self.reference['depth_pixel_uv']))


def self_check():
    """SIMULATED_OFFLINE regression: selection continuity, never physical accuracy."""
    from rgb_roi_depth import FactoryProjection
    p=np.zeros(30);p[:4]=p[4:8]=[500,500,320,240];p[8:17]=np.eye(3).ravel()
    projection=FactoryProjection(p);box=[100,100,400,240]
    raw=np.zeros((480,640),np.uint16)
    raw[120:320,120:290]=800;raw[120:320,310:480]=1100
    ref=SurfaceReference()
    assert ref.candidates(raw,box,projection)[0]['reason']=='REFERENCE_CLICK_REQUIRED'
    ref.reset([210,220]);r,_=ref.candidates(raw,box,projection)
    assert r['valid'] and r['candidate_points'][0]['raw_depth']==800
    point=r['candidate_points'][0];ref.bind(point,point['predicted_rgb_uv'])
    assert not ref.accept(1,0) and not ref.accept(1,.3)
    assert not ref.accept(2,.31) and ref.accept(3,.5)
    r,_=ref.candidates(raw,box,projection)
    assert r['candidate_points'][0]['depth_pixel_uv']==point['depth_pixel_uv']
    du,dv=point['depth_pixel_uv'];raw[dv,du]=0
    r,_=ref.candidates(raw,box,projection)
    assert r['valid'] and r['candidate_points'][0]['raw_depth']==800
    assert 0<ref.sample_offset(r['candidate_points'][0])<=ref.depth_radius_px
    raw[dv-7:dv+8,du-7:du+8]=0
    assert not ref.candidates(raw,box,projection)[0]['valid'];ref.reject()
    raw[120:320,120:290]=800
    assert ref.candidates(raw,box,projection)[0]['valid'] and ref.accept(4,.8)
    raw[120:320,120:290]=1100
    assert ref.candidates(raw,box,projection)[0]['reason']=='REFERENCE_DEPTH_CHANGED_RESELECT'
    raw[120:320,120:290]=800
    ref.reset([360,220]);r,_=ref.candidates(raw,box,projection)
    assert r['valid'] and r['candidate_points'][0]['raw_depth']==1100
    ref.reset([105,105]);assert not ref.candidates(raw,box,projection)[0]['valid']
    ref.reset([250,220]);raw[120:320,120:290]=np.linspace(800,1000,170).astype(np.uint16)[None,:]
    assert ref.candidates(raw,box,projection)[0]['valid']
    return '9 SIMULATED_OFFLINE reference cases passed: multi-layer anchor, distinct-frame acquisition, fixed pixel, neighbor, loss/recovery, wrong-layer rejection, reselect, box edge, slope'
