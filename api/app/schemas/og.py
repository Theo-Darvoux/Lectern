from typing import Literal

from pydantic import BaseModel


class OgMetaResponse(BaseModel):
    """Page metadata the web server renders into the HTML <head>."""

    site_name: str
    # Document <title>: "<resource> • <site>" for a resource, the site title otherwise.
    title: str
    og_title: str
    description: str
    type: Literal["website", "article"]
    locale: str
    # Absolute canonical URL of the page, built from FRONTEND_URL.
    url: str
    # Absolute preview image URL. Width and height are only known for the
    # generated banner; a configured OG_IMAGE_URL can be any size.
    image: str
    image_width: int | None
    image_height: int | None
    theme_color: str
    twitter_site: str | None
    twitter_creator: str | None
