from django.contrib import admin
from django.urls import path
from django.views.static import serve
from django.conf import settings
from . import views, pdf_views, image_edit_views, office_views, gpu_status
from .idle import presence
from . import video_views
admin.site.site_header='Private AI Administration'
admin.site.site_title='Account management'
admin.site.index_title='Users and permissions'
urlpatterns = [path('api/activity/', presence, name='presence')]
urlpatterns += [path('api/video/<int:pk>/control/',video_views.control,name='video-control'),path('video/',video_views.studio,name='video-studio'),path('api/video/jobs/',video_views.jobs,name='video-jobs'),path('api/video/create/',video_views.create,name='video-create'),path('api/video/<int:pk>/<str:kind>/',video_views.media,name='video-media')]
urlpatterns += [path('api/gpu/status/',gpu_status.status,name='gpu-status'),path('office/<int:pk>/',office_views.editor,name='office-editor'),path('api/office/<int:pk>/edit/',office_views.edit,name='office-edit'),path('api/office/<int:pk>/convert/',office_views.convert,name='office-convert'),path('images/<int:pk>/',image_edit_views.editor,name='image-editor'),path('api/images/<int:pk>/jobs/',image_edit_views.jobs,name='image-jobs'),path('api/images/<int:pk>/edit/',image_edit_views.create,name='image-edit'),path('api/image-jobs/<int:pk>/',image_edit_views.status,name='image-job-status'),path('pdf/<int:pk>/',pdf_views.editor,name='pdf-editor'),path('api/pdf/<int:pk>/preview/',pdf_views.preview,name='pdf-preview'),path('api/pdf/<int:pk>/info/',pdf_views.info,name='pdf-info'),path('api/pdf/<int:pk>/edit/',pdf_views.edit,name='pdf-edit'),path('',views.home,name='home'),path('login/',views.sign_in,name='login'),path('logout/',views.sign_out,name='logout'),path('setup/',views.setup,name='setup'),path('admin/',admin.site.urls),path('api/chats/',views.new_chat,name='new_chat'),path('api/chats/<int:pk>/',views.detail,name='detail'),path('api/chats/<int:pk>/send/',views.send,name='send'),path('api/chats/<int:pk>/upload/',views.upload,name='upload'),path('api/files/<int:pk>/',views.download,name='download'),path('static/<path:path>',serve,{'document_root':settings.STATIC_ROOT})]

from . import photo_views
urlpatterns += [path('photos/',photo_views.studio,name='photos'),path('api/photos/',photo_views.jobs,name='photo-jobs'),path('api/photos/create/',photo_views.create,name='photo-create'),path('api/photos/<int:pk>/cancel/',photo_views.cancel,name='photo-cancel')]

from .codex_api import gateway as codex_gateway
urlpatterns += [path('codex/v1/<path:endpoint>', codex_gateway)]
