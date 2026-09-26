from pathlib import Path
from django.conf import settings
from .photo_sizes import size_choices
MODELS = {
    'qwen21': {'label': 'Qwen-Image-2.1 · 生成与编辑 / Generate & edit', 'marker': 'QWEN21_READY', 'script': 'qwen21_infer.py', 'edit': True},
    'zimage': {'label': 'Z-Image · 写真人像 / Photorealistic portraits', 'marker': 'ZIMAGE_READY', 'script': 'zimage_infer.py', 'edit': False},
}
def ready(model):
    return model in MODELS and (Path(settings.IMAGE_EDIT_ROOT) / MODELS[model]['marker']).is_file()
def choices():
    return [dict(id=k, label=v['label'], edit=v['edit'], sizes=size_choices(k), ready=ready(k)) for k,v in MODELS.items()]
