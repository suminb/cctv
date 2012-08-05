import gdata.youtube
import gdata.youtube.service
import settings
import sys

yt_service = gdata.youtube.service.YouTubeService()

# Turn on HTTPS/SSL access.
# Note: SSL is not available at this time for uploads.
yt_service.ssl = True

yt_service.email = settings.email
yt_service.password = settings.password
yt_service.source = 'Security Video Feed'
yt_service.client_id = settings.client_id
yt_service.developer_key = settings.key
yt_service.ProgrammaticLogin()

# prepare a media group object to hold our video's meta-data
my_media_group = gdata.media.Group(
  title=gdata.media.Title(text='My Test Movie'),
  description=gdata.media.Description(description_type='plain',
                                      text='My description'),
  keywords=gdata.media.Keywords(text='security, cctv'),
  category=[gdata.media.Category(
      text='Autos',
      scheme='http://gdata.youtube.com/schemas/2007/categories.cat',
      label='Autos')],
  player=None,

  # Upload video as private
  private=gdata.media.Private()
)


# prepare a geo.where object to hold the geographical location
# of where the video was recorded
where = gdata.geo.Where()
where.set_location((37.0,-122.0))

# create the gdata.youtube.YouTubeVideoEntry to be uploaded
video_entry = gdata.youtube.YouTubeVideoEntry(media=my_media_group,
                                              geo=where)

# set the path for the video file binary
video_file_location = sys.argv[1]

new_entry = yt_service.InsertVideoEntry(video_entry, video_file_location)
