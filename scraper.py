import os
import sys
import re
import selenium
from dotenv import load_dotenv
from bs4 import BeautifulSoup
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.common.by import By
import jsonpickle
import shutil
import hashlib
import datetime
import json
from random import randint
from dotenv import load_dotenv

# Set these values in a .env file
load_dotenv()
CHROME_DRIVER = os.getenv("CHROME_DRIVER")
BUILD_JSON_PATH = os.getenv("BUILD_JSON_PATH")
RELEASE_JSON_PATH = os.getenv("RELEASE_JSON_PATH")
RELEASE_META_JSON_PATH = os.getenv("RELEASE_META_JSON_PATH")
WORKSHOP_PATH = os.getenv("WORKSHOP_PATH")
STEAM_ACCOUNTS = json.loads(os.getenv("STEAM_ACCOUNTS")) # Stored as [ ["login name", "password"], ["login name2", "password2"], ... ]
DEPOT_DOWNLOADER = os.getenv("DEPOT_DOWNLOADER")
PAGE_CACHE_PATH = os.getenv("PAGE_CACHE_PATH")

HASH_ALG = "md5"
MAPS_TO_SKIP = set([ "1567601517", "817001158", "834478221", "2070733495", "941618511" ])
MOST_RECENT_URL = "https://steamcommunity.com/workshop/browse/?appid=252950&requiredtags%5B0%5D=Maps&actualsort=mostrecent&browsesort=mostrecent&p=1"
FILEDETAILS_URL = "https://steamcommunity.com/sharedfiles/filedetails/?id={}"
WORKSHOP_URL = "https://steamcommunity.com/sharedfiles/filedetails/?id="
MAX_CACHE_AGE = 86400 # One day

DepotDownloaderCommand = "dotnet " + DEPOT_DOWNLOADER + " -app 252950 -pubfile {} -user {} -password {} -dir {}"


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


class Scraper:

    def __init__(self, pageCache):        
        options = Options()
        options.add_argument('--log-level=3')
        options.headless = True
        self.driver = selenium.webdriver.Chrome(
            options=options,
            executable_path=(CHROME_DRIVER))
        self.url = None
        self.steamAccounts = list(STEAM_ACCOUNTS)
        self.pageCache = pageCache

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
                if f.endswith(".upk") or f.endswith(".udk"):
                    return os.path.join(dirPath, f)
            # If this fails, it falls through to update

        print("Downloading workshop files for: " + str(id))
        sys.stdout.flush()
        while len(self.steamAccounts) > 0:
            steamIdx = randint(0, len(self.steamAccounts) - 1)
            steamUser, steamPass = self.steamAccounts[steamIdx]
            cmd = DepotDownloaderCommand.format(id, steamUser, steamPass, dirPath)
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
        print("Max update of " + ", ".join([str(f["updateTimestamp"]) for f in self.mapFileHistory]) + " is " + str(latestFile["updateTimestamp"]))
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
            return True
        lastUpdateDownloaded = self.maps[workshopId].getLastUpdate()
        if lastUpdateDownloaded is None:
            return True
        return lastUpdateDownloaded < lastUpdate

    def addMapData(self, workshopId, details, mapFile):
        if workshopId not in self.maps:
            self.maps[workshopId] = WorkshopMap(workshopId, details["author"], details["title"], details["desc"], details["published"], [])
        updated = details["published"] if details["lastUpdated"] is None else details["lastUpdated"]
        self.maps[workshopId].addMapFile(mapFile, updated)#, self.hashDetails)

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
        if hasUpdate:
            workshopManager.lastModified = workshopManager.lastCheck
            mapFile = scraper.getWorkshopMapFile(id, hasUpdate)
            if mapFile is None:
                continue
            
            # Add data to workshop manager
            workshopManager.addMapData(id, details, mapFile)

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

    print("\n\nScript finished. You can find the final json file in: " + RELEASE_JSON_PATH + "\n\n")
    

if __name__ == "__main__":
    main()