import os
from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.utils.deconstruct import deconstructible


@deconstructible
class PrivateMediaStorage(FileSystemStorage):
    """
    Environment-independent FileSystemStorage for private media.
    Resolves settings.PRIVATE_MEDIA_ROOT at runtime so that machine-specific
    absolute paths are not serialized into Django migrations.
    """
    def __init__(self, **kwargs):
        location = kwargs.pop('location', None)
        if location is None:
            location = str(getattr(settings, 'PRIVATE_MEDIA_ROOT', os.path.join(settings.BASE_DIR, 'private_media')))
        super().__init__(location=location, **kwargs)
