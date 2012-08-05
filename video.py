import os

class Video:
	def __init__(self, filename):
		self.filename = filename

	@property
	def file_size(self):
		"""Returns the number of bytes of the file."""
		st = os.stat(self.filename)
		return st.st_size

	@property
	def length(self):
		"""Returns the length of the video in seconds."""
		raise Exception('Not implemented')
		return 0

