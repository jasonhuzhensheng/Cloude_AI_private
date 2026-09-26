"""Server-controlled chat model choices; never accept endpoints from browsers."""
from pathlib import Path
from django.conf import settings
DEFAULT_MODEL='Qwen3.8-27B-Uncensored'
FLASH_MODEL='Qwen3.8-Flash-Next'
def choices():
    ready=(Path(settings.IMAGE_EDIT_ROOT)/'FLASH_NEXT_READY').is_file()
    return [
        {'id':DEFAULT_MODEL,'label':'Qwen3.8 27B · Uncensored','available':True},
        {'id':FLASH_MODEL,'label':'Qwen3.8 Flash Next','available':ready},
    ]
def resolve(model_id):
    if not isinstance(model_id,str):raise ValueError('Choose a valid chat model.')
    for model in choices():
        if model['id']==model_id:
            if not model['available']:raise ValueError('This model is not installed yet. Choose an available model.')
            return model
    raise ValueError('Choose a valid chat model.')

def default_model():
    return FLASH_MODEL if any(m["id"] == FLASH_MODEL and m["available"] for m in choices()) else DEFAULT_MODEL
