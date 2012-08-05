import os

class Video:
    """Encapsulation of a video file. There are two different aspects: file and video.
    'File' concerns about properties of the file itself such as file path and size (number of bytes),
    whereas 'video' is about its container (.avi, .mp4, .wmv), video codec (H.264, MPEG-4, XDiv), and
    audio codec (MP3, AC3, AAC)."""
    def __init__(self, file_path):
        self.filename = file_path

    @property
    def file_size(self):
        """Returns the number of bytes of the file."""
        st = os.stat(self.filename)
        return st.st_size

    @property
    def file_path(self):
        return self.file_path

    @property
    def video_length(self):
        """Returns the length of the video in seconds."""
        raise Exception('Not implemented')
        return 0

    @property
    def video_type(self):
        raise Exception('Not implemented')

    @property
    def audio_type(self):
        raise Exception('Not implemented')

