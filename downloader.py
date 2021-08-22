#!/usr/bin/env python3
import time
import argparse
import re
import random
import os
import datetime
import html.parser
import sys
import os.path
import shutil

import requests

class FindAlbumLink(html.parser.HTMLParser):
    def __init__(self, album_name):
        super().__init__()
        self.album_name = album_name
        self.album_path = None
        self.inside_link = False
        self.href = None

    def handle_starttag(self, tag, attrs):
        # Don't bother searching if we've already found what we're looking for
        if self.album_path is not None:
            return

        if tag == 'a':
            self.inside_link = True
            for attr, val in attrs:
                if attr == "href":
                    self.href = val
                    break

    def handle_data(self, data):
        if not self.inside_link:
            return

        data = data.strip()
        if data == self.album_name:
            self.album_path = self.href

    def handle_endtag(self, tag):
        if tag == 'a':
            self.inside_link = False
            self.href = None

class FindHighestFileNumber(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.index = None

    def handle_data(self, data):
        if self.index is not None:
            return

        index = re.match(r"# (\d+)", data.strip())
        if index is not None:
            self.index = int(index.group(1))

def request_or_exit(url, method='GET', **kwargs):
    try:
        r = requests.request(method.upper(), url, **kwargs)
        r.raise_for_status()
    except requests.ConnectionError:
        sys.exit(f"Error: could not connect to {url}; check the connection and try again")
    except requests.HTTPError as e:
        sys.exit(f"Error: server returned {e.response.status_code} {e.response.reason}")
    except requests.URLRequired:
        sys.exit("Error: invalid URL provided")
    except requests.RequestException:
        sys.exit(f"Error: Something went wrong while connecting to {url}; check the connection and try again")

    return r

def main():
    # Find the link for the album name provided
    album_path = FindAlbumLink(parsed_args.album)
    resp = request_or_exit(parsed_args.url)
    album_path.feed(resp.text)
    album_path = album_path.album_path
    if album_path is None:
        sys.exit(f"Error: could not find album '{parsed_args.album}'; Make sure it is spelled correctly and remember it is case sensitive")
    album_id = re.match(r"/(\d+)/", album_path).group(1)

    # Find the highest image index
    highest_index = FindHighestFileNumber()
    resp = request_or_exit(parsed_args.url + album_path)
    highest_index.feed(resp.text)
    highest_index = highest_index.index

    # Calculate indexes
    if highest_index is None:
        sys.exit("Error: could not find the highest file index (no files in the album?)")
    elif parsed_args.start > highest_index:
        sys.exit(f"Error: provided end index of {parsed_args.end} but highest image index is {highest_index}")

    start_index = parsed_args.start
    end_index = highest_index
    if parsed_args.end is not None and parsed_args.end < highest_index:
        end_index = parsed_args.end
     
    # Make a directory with the current time as the name
    os.mkdir(download_dir)
    zip_path = os.path.join(download_dir, "images.zip")

    # The UI uses 1 based indexing but the network comms use 0 based indexing
    # By not subtracting from end_index, it moves from an inclusive index to an exclusive one
    random.seed()
    blockstart = start_index - 1
    while True:
        if blockstart >= end_index:
            break
        # WiFi photo app can only transfer in blocks of 200
        blockend = blockstart + 200
        if blockend > end_index:
            blockend = end_index

        print(f"Downloading images {blockstart + 1} through {blockend}... ", end='', flush=True)

        # WiFi photo app appears to not like percent-encoding of data, so I 
        # have to add the content and headers manually
        selection = ','.join([str(i) for i in range(blockstart, blockend)])
        post_data = f"lib={album_id}&sel={selection}"

        resp = request_or_exit(parsed_args.url + "/startcompressing", method="POST", data=post_data, 
                headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"})

        download_code = resp.json()['selid']
        ready = resp.json()['ready']

        # Check if download is ready five times with three second breaks 
        # inbetween each check before giving up
        if not ready:
            for _ in range(5):
                time.sleep(2)
                # For some reason Wifi photo app uses random integers when requesting compression progress
                request_code = random.randrange(0, 10000000)
                resp = request_or_exit(parsed_args.url + f"/compressprogress{request_code}?{download_code}")
                if resp.json()['readyForDownload']:
                    break
            else:
                sys.exit("WiFi photo app took too long to prepare download")

        # Download and save file to disk
        resp = request_or_exit(f"http://192.168.4.104:15555/zipdownload/{download_code}/images.zip")
        with open(zip_path, 'wb') as f:
            f.write(resp.content)

        print("Done")
        print("Extracting zip file... ", end='', flush=True)

        # Extract archive
        shutil.unpack_archive(zip_path, download_dir)

        print("Done")
        print()

        blockstart = blockend

    print("Archiving directory... ", end='', flush=True)
    os.remove(zip_path)
    shutil.make_archive(download_dir, "gztar", base_dir=download_dir)
    print("Done")

    print("Deleting download directory... ", end='', flush=True)
    shutil.rmtree(download_dir)
    print("Done")

description = "Automate downloading of files from the 'WiFi Photo Transfer' app. The downloaded files will be placed in the current directory as a zip file with a current timestamp as the filename."
examples = ("examples:\n"
        "Download all files from album 'Recents':\n"
        "\t./main.py http://192.168.4.104:15555 Recents\n"
        "Download the first five files from album 'Videos':\n"
        "\t./main.py http://192.168.4.104:15555 Videos -e 5\n"
        "Download an abitrary range of 750 files:\n"
        "\t./main.py http://192.168.4.104:15555 Recents -s 1000 -e 1749\n"
        )
parser = argparse.ArgumentParser(description=description, epilog=examples, 
        formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument("url", help="URL of the webserver being run by WiFi Photo Transfer on the phone")
parser.add_argument("album", help="The name of the photo album to download images from (case sensitive)")
parser.add_argument("-s", "--start", default=1, type=int, 
        help="Specifies the start index for the range of photos to download. By default this is set to 1, which is the first image."
        )
parser.add_argument("-e", "--end", type=int, 
        help="Specifies the end index for the range of photos to download. This index is inclusive. By default this is set to the last image."
        )

parsed_args = parser.parse_args()
if parsed_args.start < 1:
    sys.exit("Error: index arguments cannot be less than 1")
elif parsed_args.end is not None:
    if parsed_args.end < 1:
        sys.exit("Error: index arguments cannot be less than 1")
    elif parsed_args.end < parsed_args.start:
        sys.exit("Error: end index cannot be less than start index")

download_dir = datetime.datetime.now().strftime("%Y%m%dT%H%M")
try:
    main()
finally:
    if os.path.exists(download_dir):
        shutil.rmtree(download_dir)
