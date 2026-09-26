import base64, io, warnings
from pathlib import Path
from PIL import Image, ImageOps, UnidentifiedImageError
IMAGE_EXTENSIONS={'.jpg','.jpeg','.png','.webp'}
def is_image(name):
    return Path(name).suffix.lower() in IMAGE_EXTENSIONS

def normalize_image(raw, *, max_pixels=24_000_000, max_edge=None):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as source:
                if source.format not in {'JPEG','PNG','WEBP'}: raise ValueError('The image must be a valid JPG, PNG, or WebP file.')
                if max_edge and max(source.size)>max_edge: raise ValueError('图片每边最多 8192 像素 / Each image edge must be at most 8192 pixels.')
                if source.width*source.height>max_pixels: raise ValueError('Image pixel count exceeds the upload limit.')
                if getattr(source,'n_frames',1)>1: raise ValueError('Please upload a static image.')
                source.load()
                image=ImageOps.exif_transpose(source)
                image.thumbnail((1280,1280))
                if image.mode in ('RGBA','LA') or 'transparency' in image.info:
                    rgba=image.convert('RGBA'); canvas=Image.new('RGB',image.size,'white'); canvas.paste(rgba,mask=rgba.getchannel('A')); image=canvas
                else: image=image.convert('RGB')
                output=io.BytesIO(); image.save(output,format='JPEG',quality=90)
                return output.getvalue()
    except (UnidentifiedImageError,OSError,Image.DecompressionBombError,Image.DecompressionBombWarning) as exc:
        raise ValueError('The image is damaged or too large. Export it as JPG, PNG, or WebP and try again.') from exc

def image_content(doc):
    with doc.file.open('rb') as file:
        encoded=base64.b64encode(file.read()).decode('ascii')
    return {'type':'image_url','image_url':{'url':('data:image/png;base64,' if Path(doc.name).suffix.lower()=='.png' else 'data:image/jpeg;base64,')+encoded}}
