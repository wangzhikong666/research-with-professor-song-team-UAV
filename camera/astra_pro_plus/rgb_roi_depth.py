"""Find measured surface candidates whose projected pixels lie in a user RGB box."""
import cv2
import numpy as np
from point_quality import assess_pixel


class FactoryProjection:
    """Preselection model only; selected points must be checked with native SDK D2C."""
    def __init__(self,parameters):
        p=np.asarray(parameters,dtype=float)
        if p.shape!=(30,) or not np.isfinite(p).all() or min(p[:2])<=0 or min(p[4:6])<=0:
            raise ValueError('Invalid actual factory parameter payload')
        self.kd=p[:4]; self.kc=p[4:8]; self.rotation=p[8:17].reshape(3,3); self.translation=p[17:20]

    def pixels(self,u,v,z):
        xyz=np.column_stack(((u-self.kd[2])*z/self.kd[0],
                             (v-self.kd[3])*z/self.kd[1],z))
        color=xyz@self.rotation.T+self.translation
        uv=color[:,:2]/color[:,2:3]*self.kc[:2]+self.kc[2:4]
        return uv,color[:,2]


def in_box(uv,box,margin=6):
    x,y,w,h=box
    return x+margin<=uv[0]<x+w-margin and y+margin<=uv[1]<y+h-margin


def surface_candidates(raw,box,projection):
    """Keep source pixels: never put a fabricated depth at an RGB box center."""
    empty=np.zeros(raw.shape,np.uint8)
    result={'valid':False,'position_m':None,'reason':'RGB_BOX_REQUIRED','candidate_points':[]}
    if box is None:
        return result,empty
    x,y,w,h=map(int,box)
    if x<0 or y<0 or x+w>640 or y+h>480 or w<24 or h<24:
        return dict(result,reason='RGB_BOX_TOO_SMALL_OR_OUTSIDE_IMAGE'),empty
    v,u=np.nonzero((raw>=600)&(raw<=4000))
    if not len(u):
        return dict(result,reason='NO_USABLE_DEPTH'),empty
    uv,zcolor=projection.pixels(u,v,raw[v,u].astype(float))
    finite=np.isfinite(uv).all(axis=1)&np.isfinite(zcolor)&(zcolor>0)
    u,v,uv,zcolor=u[finite],v[finite],uv[finite],zcolor[finite]
    uv=np.floor(uv).astype(np.int32)
    inside=(uv[:,0]>=0)&(uv[:,0]<640)&(uv[:,1]>=0)&(uv[:,1]<480)
    u,v,uv,zcolor=u[inside],v[inside],uv[inside],zcolor[inside]
    keys=uv[:,1]*640+uv[:,0]
    front=np.full(640*480,np.inf)
    np.minimum.at(front,keys,zcolor)
    # Preserve nearby samples on the same surface, but reject clearly occluded points.
    visible=zcolor<=front[keys]+5
    inside_box=(uv[:,0]>=x+6)&(uv[:,0]<x+w-6)&(uv[:,1]>=y+6)&(uv[:,1]<y+h-6)
    take=visible&inside_box
    u,v,uv=u[take],v[take],uv[take]
    if len(u)<100:
        return dict(result,reason='INSUFFICIENT_MAPPED_DEPTH',mapped_samples=int(len(u))),empty
    support=np.zeros(raw.shape,np.uint8); support[v,u]=255
    # Split abrupt depth jumps, not broad histogram bins: a continuous sloping
    # or curved surface can span many depth values and is still one patch.
    cut=np.zeros(raw.shape,bool)
    depth=raw.astype(float)
    for axis in (0,1):
        if axis==0:
            first,second=(slice(None,-1),slice(None)),(slice(1,None),slice(None))
        else:
            first,second=(slice(None),slice(None,-1)),(slice(None),slice(1,None))
        both=(support[first]>0)&(support[second]>0)
        threshold=np.maximum(30,.03*np.minimum(depth[first],depth[second]))
        jump=both&(np.abs(depth[first]-depth[second])>threshold)
        cut[first]|=jump; cut[second]|=jump
    support[cut]=0
    opened=cv2.morphologyEx(support,cv2.MORPH_OPEN,np.ones((3,3),np.uint8))
    opened[support==0]=0
    count,labels,stats,_=cv2.connectedComponentsWithStats(opened,connectivity=8)
    minimum_area=max(30,min(150,int(.02*w*h)))
    components=sorted([(int(stats[i,cv2.CC_STAT_AREA]),i) for i in range(1,count)
                       if stats[i,cv2.CC_STAT_AREA]>=minimum_area],reverse=True)
    if not components:
        return dict(result,reason='NO_DEPTH_CONTINUOUS_PATCH',mapped_samples=int(len(u))),opened
    area,label=components[0]; component=labels==label
    median=float(np.median(raw[component]))
    if len(components)>1 and components[1][0]>=.7*area:
        other_median=float(np.median(raw[labels==components[1][1]]))
        if abs(other_median-median)>80:
            return dict(result,reason='AMBIGUOUS_COMPETING_DEPTH_LAYERS',
                        candidate_areas_px=[a for a,_ in components[:2]],
                        candidate_depth_medians_mm=[median,other_median]),opened
    distance=cv2.distanceTransform(component.astype(np.uint8),cv2.DIST_L2,3)
    interior=component&(distance>=3)
    yy,xx=np.nonzero(interior&(np.abs(depth-median)<=10))
    order=np.argsort(distance[yy,xx])[::-1]
    points=[]
    for index in order[:100]:
        du,dv=int(xx[index]),int(yy[index])
        quality=assess_pixel(raw,du,dv,1,600,4000,0,10000)
        if quality['valid']:
            predicted,_=projection.pixels(np.array([du]),np.array([dv]),np.array([int(raw[dv,du])]))
            points.append({'depth_pixel_uv':[du,dv],'raw_depth':int(raw[dv,du]),
                           'predicted_rgb_uv':np.floor(predicted[0]).astype(int).tolist(),
                           'quality':quality['quality']})
            if len(points)>=25:break
    if points:
        return dict(result,valid=True,reason='SURFACE_CANDIDATES_REQUIRE_SDK_CHECK',
                    candidate_points=points,source_component_area_px=area,
                    mapped_samples=int(len(u)),surface_depth_median_mm=median,
                    surface_selection='Largest depth-continuous measured patch inside user RGB box; not semantic identity'),component.astype(np.uint8)*255
    return dict(result,reason='NO_SAFE_SURFACE_POINT',mapped_samples=int(len(u))),component.astype(np.uint8)*255


def self_check():
    # SIMULATED_OFFLINE: pipeline edge cases, never camera acceptance.
    params=np.zeros(30); params[:4]=[500,500,320,240]; params[4:8]=params[:4]
    params[8:17]=np.eye(3).ravel(); projection=FactoryProjection(params)
    raw=np.full((480,640),1500,np.uint16); raw[150:330,220:420]=800
    box=[210,140,220,200]
    result,mask=surface_candidates(raw,box,projection)
    assert result['valid'] and result['candidate_points'][0]['raw_depth']==800
    for point in result['candidate_points']:
        u,v=point['depth_pixel_uv']; assert mask[v,u] and in_box(point['predicted_rgb_uv'],box)
    assert surface_candidates(np.zeros_like(raw),box,projection)[0]['reason']=='NO_USABLE_DEPTH'
    assert surface_candidates(raw,None,projection)[0]['reason']=='RGB_BOX_REQUIRED'
    assert not surface_candidates(raw,[1,1,10,10],projection)[0]['valid']
    raw[230:250,310:330]=0
    result,_=surface_candidates(raw,box,projection)
    assert result['valid'] and all(p['raw_depth']>0 for p in result['candidate_points'])
    raw[310:330,220:420]=650
    result,_=surface_candidates(raw,box,projection)
    assert result['valid'] and result['candidate_points'][0]['raw_depth']==800
    raw[150:330,310:320]=0
    result,_=surface_candidates(raw,box,projection)
    assert result['valid'] and result['candidate_points'][0]['raw_depth']==800
    slope=np.zeros_like(raw)
    slope[110:330,110:490]=np.linspace(800,1200,380).astype(np.uint16)[None,:]
    result,_=surface_candidates(slope,[100,100,400,240],projection)
    assert result['valid'], result
    planes=np.zeros_like(raw); planes[120:320,120:290]=800; planes[120:320,310:480]=1100
    assert surface_candidates(planes,[100,100,400,240],projection)[0]['reason']=='AMBIGUOUS_COMPETING_DEPTH_LAYERS'
    return '9 SIMULATED_OFFLINE RGB-box cases passed, including continuous slope and two separate planes; not hardware acceptance'
