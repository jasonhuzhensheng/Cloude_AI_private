from PIL import Image,ImageOps
SIZES={'square':(1024,1024),'portrait':(768,1024),'landscape':(1024,768),
       'square512':(512,512),'square768':(768,768),'square1536':(1536,1536),
       'wide':(1344,768),'tall':(768,1344),'wide1536':(1536,1024),'tall1536':(1024,1536),
       'iphone2k':(1248,2688),'macbook2k':(2560,1600)}
NATIVE_MAX={
'qwen21': {'max_square':(2048,2048),'max_43':(2400,1792),'max_34':(1792,2400),'max_32':(2528,1696),'max_23':(1696,2528),'max_169':(2752,1536),'max_916':(1536,2752)},
'zimage': {'max_square':(2048,2048),'max_43':(2368,1760),'max_34':(1760,2368),'max_32':(2496,1664),'max_23':(1664,2496),'max_169':(2720,1536),'max_916':(1536,2720)}
}
def native_sizes(model='qwen21'):
    return dict(SIZES,**NATIVE_MAX[model])
WALLPAPER_LABELS={'iphone2k':'iPhone 壁纸 2K / iPhone wallpaper 2K', 'macbook2k':'MacBook 壁纸 2K / MacBook wallpaper 2K'}
def size_choices(model):
    return [dict(id=k,width=w,height=h,label=(WALLPAPER_LABELS[k]+f' · {w} × {h}') if k in WALLPAPER_LABELS else f'{w} × {h} · '+('原生 2K / Native 2K' if k.startswith('max_') else '原生 / Native')) for k,(w,h) in native_sizes(model).items()]
UPSCALES={'upscale2':2,'upscale4':4}
def output_size(format,source=None,model='qwen21'):
    sizes=native_sizes(model)
    if format in sizes:return sizes[format]
    if format not in UPSCALES or not source:raise ValueError('请选择超分原图 / Choose a source image.')
    with Image.open(source.file.path) as image:
        image=ImageOps.exif_transpose(image)
        w,h=image.size
    w*=UPSCALES[format];h*=UPSCALES[format]
    if min(w,h)<64 or max(w,h)>4096 or w*h>16777216:
        raise ValueError('超分结果最长边最多 4096 像素 / Upscaled output must be at most 4096px per edge.')
    return w,h
