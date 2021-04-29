# Rocket League Workshop Scraper

This is a modification of a script written by Naoki95957 for [RLHostBot](https://github.com/Naoki95957/RLHostBot).

The modifications add automatically downloading the workshop files, caching Steam workshop page requests to more easily resume after a failure, generating a checksum of the udk/upk map file for checksum matching, and changing the format of the outputted json.

This script was created primarily so that my Stream API plugin can identify workshop maps by their filenames and checksums in order to support all workshop loading techniques, like the map replacement technique used by Lethamyr's custom map loading appliation.

This repository was created in order to more easily push updates to the json file automatically, so that it can be retrieved by the plugin. The script itself isn't very robust or user friendly.