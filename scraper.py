import os
import sys
import re
import selenium
from dotenv import load_dotenv
from bs4 import BeautifulSoup
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.firefox.firefox_binary import FirefoxBinary
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.common.by import By
import jsonpickle
import shutil
import hashlib
import datetime
import json
from random import randint
from dotenv import load_dotenv
import subprocess
import shlex
import io
import time
from lxml import etree
import zipfile
from google_drive_downloader import GoogleDriveDownloader as gdd

# Set these values in a .env file
load_dotenv()
CHROME_DRIVER = os.getenv("CHROME_DRIVER")
BUILD_JSON_PATH = os.getenv("BUILD_JSON_PATH")
RELEASE_JSON_PATH = os.getenv("RELEASE_JSON_PATH")
RELEASE_META_JSON_PATH = os.getenv("RELEASE_META_JSON_PATH")
WORKSHOP_PATH = os.getenv("WORKSHOP_PATH")
STEAM_WORKSHOP_PATH = os.getenv("STEAM_WORKSHOP_PATH") # Something like: C:\Program Files (x86)\Steam\steamapps\workshop\content\252950
STEAM_ACCOUNTS = json.loads(os.getenv("STEAM_ACCOUNTS")) # Stored as [ ["login name", "password"], ["login name2", "password2"], ... ]
DEPOT_DOWNLOADER = os.getenv("DEPOT_DOWNLOADER")
PAGE_CACHE_PATH = os.getenv("PAGE_CACHE_PATH")

HASH_ALG = "md5"
MAPS_TO_SKIP = set([ "1567601517", "817001158", "834478221", "2070733495", "941618511", "2395273453" ])
MOST_RECENT_URL = "https://steamcommunity.com/workshop/browse/?appid=252950&browsesort=mostrecent&section=items&actualsort=mostrecent&p=1"
FILEDETAILS_URL = "https://steamcommunity.com/sharedfiles/filedetails/?id={}"
WORKSHOP_URL = "https://steamcommunity.com/sharedfiles/filedetails/?id="
LETHS_MAPS_START_URL = "https://lethamyr.com/mymaps"
MAX_CACHE_AGE = 86400 # One day

DepotDownloaderCommand = "dotnet " + DEPOT_DOWNLOADER + " -app 252950 -pubfile {} -user {} -password {} -dir {}"

# Make sure some paths exist
os.makedirs(os.path.dirname(BUILD_JSON_PATH), exist_ok=True)
os.makedirs(os.path.dirname(RELEASE_JSON_PATH), exist_ok=True)
os.makedirs(os.path.dirname(RELEASE_META_JSON_PATH), exist_ok=True)
os.makedirs(PAGE_CACHE_PATH, exist_ok=True)

def clean_path(string):
    for c in ":*\"/\\[];|,":
        string = string.replace(c, '')
    return string

def clean_str(string):
    return str(re.sub(r"^\s+", '', string))

def clean_datetime(ts):
    date, time = ts.split('@')
    fields = date.split(', ')
    if len(fields) == 1:
        now = datetime.datetime.now()
        fields.append(now.year)
    dt = datetime.datetime.strptime(f"{fields[0]} {fields[1]} {time}", "%b %d %Y %I:%M%p")
    return int(dt.timestamp())

def mapFilePath(map, mapFile):
    fp = os.path.join(WORKSHOP_PATH, map.workshopId, mapFile["filename"])
    if os.path.exists(fp):
        return fp
    fp = os.path.join(WORKSHOP_PATH, map.workshopId, mapFile["filename"])
    if os.path.exists(fp):
        return fp
    return None


class PageCache:

    def __init__(self):
        if not os.path.exists(PAGE_CACHE_PATH):
            os.makedirs(PAGE_CACHE_PATH)
        self.cacheTime = int(datetime.datetime.now().timestamp())

        mostRecentCache = 0
        for cacheDir in os.listdir(PAGE_CACHE_PATH):
            if os.path.isdir(os.path.join(PAGE_CACHE_PATH, cacheDir)):
                try:
                    cacheTime = int(cacheDir)
                    mostRecentCache = max(cacheTime, mostRecentCache)
                except ValueError:
                    pass
        if self.cacheTime - mostRecentCache <= MAX_CACHE_AGE:
            self.cacheTime = mostRecentCache
        else:
            os.makedirs(os.path.join(PAGE_CACHE_PATH, str(self.cacheTime)))

    def getWorkshopMapPage(self, workshopId):
        fpath = os.path.join(PAGE_CACHE_PATH, str(self.cacheTime), workshopId)
        if os.path.exists(fpath):
            with open(fpath, 'r', encoding='utf-8') as fp:
                return fp.read()

    def setWorkshopMapPage(self, workshopId, data):
        fpath = os.path.join(PAGE_CACHE_PATH, str(self.cacheTime), workshopId)
        with open(fpath, 'wb') as fp:
            fp.write(data.encode('utf-8'))

    def getLethMapPage(self, link):
        fpath = os.path.join(PAGE_CACHE_PATH, str(self.cacheTime), link[link.rfind('/') + 1: ])
        if os.path.exists(fpath):
            with open(fpath, 'r', encoding='utf-8') as fp:
                return fp.read()

    def setLethMapPage(self, link, data):
        fpath = os.path.join(PAGE_CACHE_PATH, str(self.cacheTime), link[link.rfind('/') + 1: ])
        with open(fpath, 'wb') as fp:
            fp.write(data.encode('utf-8'))


class Scraper:

    def __init__(self, pageCache):        
        if 'chrome' in CHROME_DRIVER:
            chrome_options = selenium.webdriver.ChromeOptions()
            chrome_options.add_argument('--no-sandbox')
            chrome_options.add_argument('--headless')
            chrome_options.add_argument('--disable-gpu')
            self.driver = selenium.webdriver.Chrome(chrome_options=chrome_options)
        elif 'gecko' in CHROME_DRIVER:
            #options = FirefoxOptions()
            #options.add_argument("--headless")
            #options.binary_location = "./"
            #self.driver = selenium.webdriver.Firefox(
            #    options=options)
            options = FirefoxOptions()
            options.add_argument('--headless')
            self.driver = selenium.webdriver.Firefox(firefox_binary=FirefoxBinary('/workshop-maps/geckodriver'), firefox_options=options)
        self.url = None
        self.steamAccounts = list(STEAM_ACCOUNTS)
        self.pageCache = pageCache

    def __del__(self):
        print("Killing selenium driver")
        self.driver.quit()

    def getWorkshopIDs(self):
        url = MOST_RECENT_URL
        ids = set()
        while True:
            print(f"Retrieving: {url}")
            sys.stdout.flush()
            self.driver.get(url)
            try:
                WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((
                        By.ID, 'rightContents')))
            except Exception as e:
                print("FAILED TO GET WORKSHOP IDS FROM -> " + url)
                sys.stdout.flush()
                continue

            soup = BeautifulSoup(self.driver.page_source, "html.parser")
            workshopItems = soup.find('div', { 'class': 'workshopBrowseItems' })
            for a in workshopItems.findAll('a'):
                link = a['href']
                if 'filedetails' in link:
                    workshopID = link[link.rfind('id=') + 3 : link.rfind('&')]
                    ids.add(workshopID)
            paging = soup.find('div', { 'class': 'workshopBrowsePagingControls' })
            url = None
            for btn in paging.findAll('a'):
                if btn.contents[0] == '>':
                    url = btn['href']
            if url is None:
                break
            
        return list(ids)

    def getWorkshopDetails(self, id):
        print("Getting workshop details for: " + str(id))
        cacheData = self.pageCache.getWorkshopMapPage(id)
        soup = None
        if cacheData is None:
            self.driver.get(FILEDETAILS_URL.format(id))
            try:
                WebDriverWait(self.driver, 10).until(
                        EC.presence_of_element_located((
                            By.ID, 'rightContents')))
            except Exception as e:
                print(f"DROPPED STEAM MAP -> {id}")
                sys.exit()

            soup = BeautifulSoup(self.driver.page_source, "html.parser")
            self.pageCache.setWorkshopMapPage(id, self.driver.page_source)
        else:
            print("Retrieved page from cache")
            soup = BeautifulSoup(cacheData, "html.parser")
        
        author_element = soup.find('div', {'class':'friendBlockContent'})
        title_element = soup.find('div', {'class':'workshopItemTitle'})
        description_element = soup.find('div', {'class':'workshopItemDescription', 'id':'highlightContent'})
        detailsLeft = soup.find('div', { 'class': 'detailsStatsContainerLeft' })
        detailsRight = soup.find('div', { 'class': 'detailsStatsContainerRight' })

        if author_element is None or title_element is None or description_element is None or detailsLeft is None or detailsRight is None:
            print(f"FAILED TO GET SOME DATA FOR -> {id}")
            return None

        
        author = clean_str(author_element.contents[0])
        title = clean_str(title_element.contents[0])
        
        desc = description_element.get_text('\n')
        desc = desc.replace("~~", "")

        published = None
        lastUpdated = None
        for i, div in enumerate(detailsLeft.findAll('div')):
            if div.get_text().strip().lower() == "posted":
                divsRight = detailsRight.findAll("div")
                if len(divsRight) > i and '@' in divsRight[i].get_text():
                    published = clean_datetime(divsRight[i].get_text())
            elif div.get_text().strip().lower() == "updated":
                divsRight = detailsRight.findAll("div")
                if len(divsRight) > i and '@' in divsRight[i].get_text():
                    lastUpdated = clean_datetime(divsRight[i].get_text())

        if published is None:
            print(f"FAILED TO GET PUBLISHED FOR -> {id}")
            return None

        if lastUpdated is None:
            lastUpdated = published

        return { "title": title, "author": author, "desc": desc, "published": published, "lastUpdated": lastUpdated }

    def getWorkshopMapFile(self, id, doUpdate):
        dirPath = os.path.join(WORKSHOP_PATH, id)
        if not doUpdate:
            print(f"No new update for {id}")
            for f in os.listdir(dirPath):
                if f.endswith(".upk") or f.endswith(".udk") or f.endswith('.umap'):
                    return os.path.join(dirPath, f)
            # If this fails, it falls through to update

        print("Downloading workshop files for: " + str(id))
        sys.stdout.flush()
        while len(self.steamAccounts) > 0:
            steamIdx = randint(0, len(self.steamAccounts) - 1)
            steamUser, steamPass = self.steamAccounts[steamIdx]
            cmd = DepotDownloaderCommand.format(id, steamUser, steamPass, dirPath)

            process = subprocess.Popen(shlex.split(cmd), stdout=subprocess.PIPE)
            mapFiles = []
            lines = []
            while True:
                line = process.stdout.readline()
                if not line or (line == '' and process.poll() is not None):
                    break
                if line:
                    line = line.decode('utf-8').strip()
                    lines.append(line)
                    if (".udk" in line or ".upk" in line or ".umap" in line) and WORKSHOP_PATH in line:
                        mapFiles.append(line[line.find(WORKSHOP_PATH):].replace('\n',''))
                    elif "RateLimitedExceeded" in line:
                        self.steamAccounts = self.steamAccounts[:steamIdx] + self.steamAccounts[steamIdx + 1:]
                    elif "Encountered error" in line and "NotFound" in line:
                        process.kill()
                        print(f"ABORTING DOWNLOAD FOR -> {id}. Error: {line}")
                        return None
            if len(mapFiles) == 0:
                print(f"FAILED TO GET MAP FILE FOR -> {id}. Command: {cmd}")
                print('\n'.join(lines))
                return None
            return self.identifyMapFromFiles(mapFiles)

            '''
            stream = os.popen(cmd)
            output = stream.readlines()
            mapFile = None
            for line in output:
                if (".udk" in line or ".upk" in line) and WORKSHOP_PATH in line:
                    mapFile = line[line.find(WORKSHOP_PATH):].replace('\n','')
                if "RateLimitExceeded" in line:
                    self.steamAccounts = self.steamAccounts[:steamIdx] + self.steamAccounts[steamIdx + 1:]
            if mapFile is None:
                print(f"FAILED TO GET MAP FILE FOR -> {id}. Command: {cmd}")
                print(''.join(output))
            return mapFile
            '''

    def getWorkshopMapFileFromSteamFolder(self, workshopId):
        dirPath = os.path.join(STEAM_WORKSHOP_PATH, workshopId)
        print(f"Attempting to copy map file from steam workshop path for: {workshopId}")
        if os.path.exists(dirPath):
            mapFiles = [ os.path.join(dirPath, f) for f in filter(lambda x: x.endswith('.udk') or x.endswith('.upk') or x.endwith('.umap'), os.listdir(dirPath)) ]
            if len(mapFiles) == 0:
                print(f"FAILED TO GET MAP FILE IN STEAM WORKSHOP PATH FOR -> {workshopId}")
                return None
            mapFile = self.identifyMapFromFiles(mapFiles)
            target = os.path.join(WORKSHOP_PATH, workshopId, os.path.basename(mapFile))
            if not os.path.exists(mapFile):
                os.makedirs(os.path.join(WORKSHOP_PATH, workshopId))
                print(f"Copying file {mapFile} to {target}")
                shutil.copyfile(mapFile, target)
            return mapFile
        else:
            print(f"FAILED TO LOCATE FOLDER IN STEAM WORKSHOP PATH FOR -> {workshopId}")


    def identifyMapFromFiles(self, mapFiles):
        udks = list(filter(lambda x: ".udk" in x, mapFiles))
        mapFiles = udks if len(udks) > 0 else mapFiles
        if len(mapFiles) == 1:
            return mapFiles[0]
        largestMapFile = { "file": None, "size": 0 }
        for f in mapFiles:
            size = os.path.getsize(f)
            if size > largestMapFile["size"]:
                largestMapFile = { "file": f, "size": size }
        return largestMapFile["file"]

    
    def getLethMaps(self):
        url = LETHS_MAPS_START_URL
        links = []
        while True:
            print(f"Retrieving: {url}")
            sys.stdout.flush()

            self.driver.get(url)
            time.sleep(5)

            with open('cache_file', 'w') as fout:
                fout.write(self.driver.page_source)

            soup = BeautifulSoup(self.driver.page_source, "html.parser")
            articles = soup.findAll('article', { 'class': 'blog-item' })
            for article in articles:
                for a in article.findAll('a', { 'class': 'blog-more-link' }):
                    links.append("https://lethamyr.com" + a['href'])

            pagination = soup.find('nav', { 'class': 'blog-list-pagination' })
            if pagination is None:
                print(f"Failed to find pagination in {url}. May have missed some maps.")
                return links
            
            older = pagination.find('div', { 'class': 'older' })
            if older is None:
                print(f"Failed to find older posts in {url}. May have missed some maps.")
                return links

            olderPosts = older.find('a')
            if olderPosts is None:
                return links # We should be done if this is missing
            
            url = "https://lethamyr.com" + olderPosts["href"]
    

    def getLethMapDetails(self, link):
        print("Getting leth map details for: " + link)
        cacheData = self.pageCache.getLethMapPage(link)
        if cacheData is None:
            self.driver.get(link)
            time.sleep(2)

            dom = etree.HTML(self.driver.page_source)
            self.pageCache.setLethMapPage(link, self.driver.page_source)
        else:
            print("Retrieved page from cache")
            dom = etree.HTML(cacheData)

        titleEl = dom.xpath('//h1[@data-content-field="title"]')
        descEl = dom.xpath('//h3[text()="Description"]/following-sibling::p')
        downloadLink = dom.xpath('//a[text()="Download"]')

        if len(titleEl) == 0 or len(descEl) == 0 or len(downloadLink) == 0:
            print(f"FAILED TO GET MAP DETAILS FOR -> {link}")
            return None

        return { "title": titleEl[0].text, "desc": descEl[0].text, "link": link, "download": downloadLink[0].attrib['href'] }

    
    def getLethMapFile(self, mapDetails):
        fileId = mapDetails["download"]
        fileId = fileId[fileId.rfind("/file/d/") + 8 : fileId.rfind("/")]
        dest = os.path.join(WORKSHOP_PATH, clean_path(mapDetails["title"].replace(" ", "-")) + ".zip")
        destFolder = os.path.join(WORKSHOP_PATH, clean_path(mapDetails["title"]))

        if not os.path.exists(dest):
            gdd.download_file_from_google_drive(file_id=fileId, dest_path=dest, unzip=True)

        # Do folder rename before processing files as the folder might not have been extracted this run
        if os.path.exists(dest) and not os.path.exists(destFolder):
            folderName = zipfile.ZipFile(dest).namelist()[0]
            folderName = folderName[: folderName.find('/')]
            if folderName != clean_path(mapDetails["title"]):
                shutil.move(os.path.join(WORKSHOP_PATH, folderName), destFolder)

        mapFile = None
        if os.path.exists(destFolder):
            for f in os.listdir(destFolder):
                if f.endswith(".udk"):
                    mapFile = f
                elif f.endswith(".json"):
                    with open(os.path.join(destFolder, f)) as fp:
                        js = json.load(fp)
                        mapDetails["author"] = js["author"]
                        mapDetails["desc"] = js["desc"]            
        
        if mapFile is None:
            return None
        mapDetails["filename"] = mapFile
        return mapDetails


class HashDetails:

    def __init__(self, algorithm, segment):
        self.algorithm = algorithm
        self.segment = segment

    def computeHashes(self, fpath):
        fullHash = hashlib.md5()
        with open(fpath, mode='rb') as fp:
            fp.seek(self.segment["offset"])
            fullHash.update(fp.read(self.segment["length"]))
        return [ fullHash.hexdigest(), self.computeSegmentHash(fpath) ]

    def computeSegmentHash(self, fpath):
        # Some maps have been reuploaded so we can't depend on them being unique. Instead, just prefix it with filesize to avoid collisions.
        segmentHash = hashlib.md5()
        fsize = os.path.getsize(fpath)
        with open(fpath, mode='rb') as fp:
            fp.seek(self.segment["offset"])
            segmentHash.update(fp.read(self.segment["length"]))
        return str(fsize) + ":" + segmentHash.hexdigest()

    @staticmethod
    def computeFullHash(fpath):
        fullHash = hashlib.md5()
        with open(fpath, mode='rb') as fp:
            fullHash.update(fp.read())
            return fullHash.hexdigest()


class WorkshopMap:

    def __init__(self, workshopId, author, title, desc, published, mapFileHistory):
        self.workshopId = workshopId
        self.author = author
        self.title = title
        self.desc = desc
        self.published = published
        self.mapFileHistory = mapFileHistory

    # Copies existing map files into a directory named after the latest timestamp in mapFileHistory
    # This is done to maintain those files as DepotDownloader might update them with a new version
    # We might need to change the segment, so this makes it possible to change history
    def backupExistingFiles(self):
        mapFile = self.getLatestMapFile()
        if mapFile is None:
            return
        newPath = os.path.join(WORKSHOP_PATH, self.workshopId, mapFile["updateTimestamp"])
        if not os.path.exists(newPath):
            os.makedirs(newPath)

        for f in os.listdir(os.path.join(WORKSHOP_PATH, self.workshopId)):
            if os.path.isdir(f): continue
            if os.path.exists(os.path.join(newPath, f)): continue
            shutil.copyfile(os.path.join(WORKSHOP_PATH, f), newPath)

    def getLatestMapFile(self):
        if len(self.mapFileHistory) == 0:
            return None
        latestFile = self.mapFileHistory[0]
        for f in self.mapFileHistory:
            if f["updateTimestamp"] > latestFile["updateTimestamp"]:
                latestFile = f
        return latestFile

    def getLastUpdate(self):
        lastestFile = self.getLatestMapFile()
        if lastestFile is None:
            return 0
        return lastestFile["updateTimestamp"]

    def addMapFile(self, mapFile, updateTimestamp):#, hashDetails):
        if updateTimestamp < self.getLastUpdate():
            return
        #fullHash, segmentHash = hashDetails.computeHashes(mapFile)
        fullHash = HashDetails.computeFullHash(mapFile)
        self.mapFileHistory.append({
            "filename": os.path.basename(mapFile),
            "fullHash": fullHash,
            #"segmentHash": segmentHash, # Fuck it, just use full hash
            "updateTimestamp": updateTimestamp
        })


class WorkshopManager:

    @staticmethod
    def fromJson(jsonPath):
        if os.path.exists(jsonPath):
            with open(jsonPath, 'r') as fp:
                wm = jsonpickle.decode(fp.read())
                shutil.copyfile(BUILD_JSON_PATH, f"{BUILD_JSON_PATH}.{wm.lastCheck}.json")
                return wm
        else:
            return WorkshopManager(None, None, [], { "algorithm": HASH_ALG, "segment": { "offset": 0, "length": 1024 } })

    def __init__(self, lastChecked, lastModified, maps, hashDetails):
        self.lastCheck = lastChecked
        self.lastModified = lastModified
        self.maps = { m.workshopId: WorkshopMap(**m) for m in maps }
        #self.hashDetails = HashDetails(**hashDetails)

    def mapHasUpdate(self, workshopId, lastUpdate):
        if workshopId not in self.maps:
            print("workshopId not in maps")
            return True
        lastUpdateDownloaded = self.maps[workshopId].getLastUpdate()
        if lastUpdateDownloaded is None:
            print("lastUpdateDownloaded is None")
            return True
        print(f"comparing {lastUpdateDownloaded} < {lastUpdate}: {lastUpdateDownloaded < lastUpdate}")
        return lastUpdateDownloaded < lastUpdate

    def addMapData(self, workshopId, details, mapFile):
        if workshopId not in self.maps:
            self.maps[workshopId] = WorkshopMap(workshopId, details["author"], details["title"], details["desc"], details["published"], [])
        updated = details["published"] if details["lastUpdated"] is None else details["lastUpdated"]
        self.maps[workshopId].addMapFile(mapFile, updated)#, self.hashDetails)

    def addLethMapData(self, details):
        details["fullHash"] = HashDetails.computeFullHash(os.path.join(WORKSHOP_PATH, clean_path(details["title"]), details["filename"]))
        self.maps[details["title"]] = details

    def getSmallestMapFileSize(self):
        smallest = -1
        for id, m in self.maps.items():
            for i, mapFile in enumerate(m.mapFileHistory):
                mapFilePath = os.path.join(WORKSHOP_PATH, m.workshopId, str(mapFile["updateTimestamp"]), mapFile["filename"])
                if i == len(m.mapFileHistory) - 1:
                    mapFilePath = os.path.join(WORKSHOP_PATH, m.workshopId, mapFile["filename"])
                if not os.path.exists(mapFilePath):
                    print("PATH DOESNT EXIST FOR " + mapFilePath)
                    print("MAP FILE HISTORY: " + jsonpickle.encode(m.mapFileHistory))
                s = os.path.getsize(mapFilePath)
                if s == 0: print("File has size 0? " + mapFilePath)
                if smallest > s or smallest < 0:
                    smallest = s
        return smallest

    def allSegmentHashesUnique(self):
        segmentHashes = set()
        for id, m in self.maps.items():
            thisMapsSegments = []
            for mapFile in m.mapFileHistory:
                if mapFile["segmentHash"] in segmentHashes:
                    return False
                thisMapsSegments.append(mapFile["segmentHash"])
            for s in thisMapsSegments:
                segmentHashes.add(s)
        return True

    '''def generateUniqueSegmentHashes(self):
        # This function defunc'd and filled with debug prints and other shit. We no longer care about unique hashes.
        if self.allSegmentHashesUnique():
            return
        smallestFile = self.getSmallestMapFileSize()
        skipThisOffset = False
        for m in range(0, int((smallestFile - 1024) / 256)):
            segLen = 1024 + 256*m
            self.hashDetails.segment["length"] = segLen
            for i in range(0, int(smallestFile / segLen)):
                if m == 0 and i == 0: continue
                self.hashDetails.segment["offset"] = segLen * i
                segmentHashes = set()
                skipThisOffset = False # Used to double break outside of offset loop
                for id, m in self.maps.items():
                    thisMapsSegments = []
                    for mapFile in m.mapFileHistory:
                        fp = mapFilePath(m, mapFile)
                        if fp is None:
                            print(f"FAILED TO FIND FILEPATH FOR -> {id}")
                            sys.exit()
                        segHash = self.hashDetails.computeSegmentHash(fp)
                        if segHash in segmentHashes:
                            skipThisOffset = True
                            break
                        else:
                            thisMapsSegments.append(segHash)
                            mapFile["segmentHash"] = segHash
                    if skipThisOffset:
                        break
                    for s in thisMapsSegments:
                        segmentHashes.add(s)
        if skipThisOffset or not self.allSegmentHashesUnique():
            with open('build/non-unique-hashes.json', 'w') as fp:
                fp.write(jsonpickle.encode(self))
            print(f"FAILED TO FIND UNIQUE HASH SEGMENT")
            sys.exit()
    '''


def main():
    print("\n\nTHIS SCRIPT ISN'T VERY USER FRIENDLY AND I WOULDN'T CONSIDER IT A \"RELEASE\" VERSION.")
    print("PLEASE READ IF THIS IS YOUR FIRST TIME RUNNING THIS.")
    print("When using DepotDownloader for the first time with a steam account, you will likely need to provide an authentication code.")
    print("This script does not expose the input for entering the code. You can either run the following command for each account, or monitor the script and when it looks like it's stuck downloading a map, find the code and enter it into the console.")
    print(DepotDownloaderCommand.format("964271505", "<username>", "<password>", os.path.join(WORKSHOP_PATH, "964271505")))
    print("\n\nNote that accounts get rate limited by Steam every so often, and it works better if you have multiple Steam accounts that own Rocket League. I had several accounts and never tested if you can family share or not. You could always manually get all the maps yourself, and then remove the line that attempts to download them.")
    print("\n\n")
    sys.stdout.flush()

    scraper = Scraper(PageCache())
    workshopManager = WorkshopManager.fromJson(BUILD_JSON_PATH)
    workshopManager.lastCheck = int(datetime.datetime.now().timestamp())
    
    ids = []
    if "skipSteam" not in sys.argv:
        ids = scraper.getWorkshopIDs()

        print(f"Processing {len(ids)} maps")

        for id in ids:
            if id in MAPS_TO_SKIP:
                continue

            # Get details
            sys.stdout.flush()
            details = scraper.getWorkshopDetails(id)
            if details is None:
                continue

            # Get workshop map file
            hasUpdate = workshopManager.mapHasUpdate(id, details["lastUpdated"])
            hasUpdate = False
            if hasUpdate:
                workshopManager.lastModified = workshopManager.lastCheck
                mapFile = scraper.getWorkshopMapFile(id, hasUpdate)
                if mapFile is None:
                    mapFile = scraper.getWorkshopMapFileFromSteamFolder(id)
                    if mapFile is None:
                        continue
                
                # Add data to workshop manager
                workshopManager.addMapData(id, details, mapFile)

    lethMapLinks = []
    if "skipLeth" not in sys.argv:
        # TODO: Some maps have more than one download (spaceship)
        lethMapLinks = scraper.getLethMaps()

        print(f"Processing {len(lethMapLinks)} leth maps")

        for link in lethMapLinks:
            sys.stdout.flush()
            details = scraper.getLethMapDetails(link)
            if details is None:
                continue

            details = scraper.getLethMapFile(details)
            if details is None:
                continue

            del details["download"]
            workshopManager.addLethMapData(details)

    # Find segments in map files that produce a unique hash
    #workshopManager.generateUniqueSegmentHashes()

    # Save results
    with open(BUILD_JSON_PATH, 'w') as fp:
        fp.write(jsonpickle.encode(workshopManager))
    with open(RELEASE_JSON_PATH, 'w') as fp:
        fp.write(jsonpickle.encode(workshopManager, unpicklable=False))
    with open(RELEASE_META_JSON_PATH, 'w') as fp:
        json.dump({
            "lastCheck": workshopManager.lastCheck,
            "lastModified": workshopManager.lastModified
        }, fp)

    workshopIds = set(ids)
    lethMapLinksSet = set(lethMapLinks)
    for id in workshopManager.maps:
        map = workshopManager.maps[id]
        if type(map) is WorkshopMap:
            workshopIds.discard(id)
        else:
            lethMapLinksSet.discard(map["link"])
    for id in MAPS_TO_SKIP:
        workshopIds.discard(id)

    print("Workshop IDs missing from maps.json: \n\t" + "\n\t".join(list(workshopIds)))
    print("\n\nLeth maps missing from maps.json: \n\t" + "\n\t".join(list(lethMapLinksSet)))

    print("\n\nScript finished. You can find the final json file in: " + RELEASE_JSON_PATH + "\n\n")
    

if __name__ == "__main__":
    main()