from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.http import MediaIoBaseDownload, HttpRequest
from colorama import Fore, Style
import io, os, shutil, uuid, sys, json, time
import google_auth_httplib2

FOLDER = 'application/vnd.google-apps.folder'
DEBUG = False
CHUNK_SIZE = 20 * 1024 * 1024 # 20MB chunks

DEBUG_STATEMENTS = [] # cache all debug statements

def debug_write(logfile):
    with open(logfile, 'w') as f:
        f.write('\n'.join(DEBUG_STATEMENTS))
    print(f"{Fore.YELLOW}DEBUG LOG SAVED HERE:{Style.RESET_ALL} {logfile}")

def list_td(service):
    # Call the Drive v3 API
    results = service.drives().list(pageSize=100).execute()

    if not results['drives']:
        return None
    else:
        return results['drives']

def iterfiles(service, name=None, is_folder=None, parent=None, order_by='folder,name,createdTime'):
    q = []
    if name is not None:
        q.append("name = '%s'" % name.replace("'", "\\'"))
    if is_folder is not None:
        q.append("mimeType %s '%s'" % ('=' if is_folder else '!=', FOLDER))
    if parent is not None:
        q.append("'%s' in parents" % parent.replace("'", "\\'"))
    params = {'pageToken': None, 'orderBy': order_by, 'includeItemsFromAllDrives': True, 'supportsAllDrives': True}
    if q:
        params['q'] = ' and '.join(q)
    while True:
        response = service.files().list(**params).execute()
        for f in response['files']:
            yield f
        try:
            params['pageToken'] = response['nextPageToken']
        except KeyError:
            return

def walk(service, top='root', by_name=False):
    if by_name:
        top, = iterfiles(service, name=top, is_folder=True)
    else:
        top = service.files().get(fileId=top, supportsAllDrives=True).execute()
        if top['mimeType'] != FOLDER:
            raise ValueError('not a folder: %r' % top)
    stack = [((top['name'],), top)]
    print(f"Indexing: {Fore.YELLOW}{top['name']}{Style.RESET_ALL}\nFolder ID: {Fore.YELLOW}{top['id']}{Style.RESET_ALL}\n")
    while stack:
        path, top = stack.pop()
        dirs, files = is_file = [], []
        for f in iterfiles(service, parent=top['id']):
            is_file[f['mimeType'] != FOLDER].append(f)
        yield path, top, dirs, files
        if dirs:
            stack.extend((path + (d['name'],), d) for d in reversed(dirs))

def querysearch(service, name=None, drive_id=None, is_folder=None, parent=None, order_by='folder,name,createdTime'):
    q = []
    items = []
    if name is not None:
        q.append("name contains '%s'" % name.replace("'", "\\'"))
    if is_folder is not None:
        q.append("mimeType %s '%s'" % ('=' if is_folder else '!=', FOLDER))
    if parent is not None:
        q.append("'%s' in parents" % parent.replace("'", "\\'"))
    if drive_id == None:
        params = {'pageToken': None, 'orderBy': order_by, 'includeItemsFromAllDrives': True, 'supportsAllDrives': True}
    else:
        params = {'pageToken': None, 'orderBy': order_by, 'includeItemsFromAllDrives': True, 'supportsAllDrives': True, 'corpora': 'allDrives'}
    if q:
        params['q'] = ' and '.join(q)
    while len(items) < 10:
        response = service.files().list(**params).execute()
        for f in response['files']:
            items.append(f)
        try:
            params['pageToken'] = response['nextPageToken']
        except KeyError:
            break
    return items

def download(service, file, destination, skip=False, abuse=False, noiter=False):
    # add file extension if we don't have one
    mimeType = file['mimeType']
    if "application/vnd.google-apps" in mimeType:
        if "shortcut" in mimeType:
            ext_file = '.googleshortcut'
        elif "form" in mimeType:
            ext_file = '.googleform.zip'
        elif "drawing" in mimeType:
            ext_file = '.googledrawing.svg'
        elif "script" in mimeType:
            ext_file = '.googlescript.json'
        elif "site" in mimeType:
            ext_file = '.googlesite.txt'
        elif "jam" in mimeType:
            ext_file = '.googlejam.pdf'
        elif "mail-layout" in mimeType:
            ext_file = '.googlemaillayout.txt'
        elif "scenes" in mimeType:
            ext_file = '.googlescenes.mp4'
        elif "document" in mimeType:
            ext_file = '.docx'
        elif "spreadsheet" in mimeType:
            ext_file = '.xlsx'
        elif "presentation" in mimeType:
            ext_file = '.pptx'
        else:
            ext_file = '.unknown'
        root, ext = os.path.splitext(file['name'])
        if not ext:
            file['name'] = file['name'] + ext_file
    # file is a dictionary with file id as well as name
    if skip and os.path.exists(os.path.join(destination, file['name'])):
        return -1
    resolved_mime_type = 'text/plain'
    if "application/vnd.google-apps" in mimeType:
        if "shortcut" in mimeType:
            print(f"{Fore.GREEN}Creating shortcut{Style.RESET_ALL} {file['name']} ...")
            os.makedirs(destination, exist_ok=True)
            try:
                with open(os.path.join(destination, file['name']), "w") as fh:
                    fh.write(file['shortcutDetails']['targetId'])
            except:
                print(f"{Fore.RED}Could not write shortcut{Style.RESET_ALL} {file['name']} ...")
            return 0
        elif "form" in mimeType:
            resolved_mime_type = 'application/zip'
        elif "drawing" in mimeType:
            resolved_mime_type = 'image/svg+xml'
        elif "script" in mimeType:
            resolved_mime_type = 'application/vnd.google-apps.script+json'
        elif "site" in mimeType:
            resolved_mime_type = 'text/plain'
        elif "jam" in mimeType:
            resolved_mime_type = 'application/pdf'
        elif "mail-layout" in mimeType:
            resolved_mime_type = 'text/plain'
        elif "scenes" in mimeType:
            resolved_mime_type = 'video/mp4'
        elif "document" in mimeType:
            resolved_mime_type = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        elif "spreadsheet" in mimeType:
            resolved_mime_type = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        elif "presentation" in mimeType:
            resolved_mime_type = 'application/vnd.openxmlformats-officedocument.presentationml.presentation'
        else:
            resolved_mime_type = 'text/plain'
            print(f"{Fore.YELLOW}Bailout type used for file{Style.RESET_ALL} {file['name']} ...")
        dlfile = service.files().export_media(fileId=file['id'], mimeType=resolved_mime_type)
    else:
        dlfile = service.files().get_media(fileId=file['id'], supportsAllDrives=True, acknowledgeAbuse=abuse)
    rand_id = str(uuid.uuid4())
    os.makedirs('buffer', exist_ok=True)
    fh = io.FileIO(os.path.join('buffer', rand_id), 'wb')
    downloader = MediaIoBaseDownload(fh, dlfile, chunksize=CHUNK_SIZE)
    if noiter: print(f"{Fore.GREEN}Downloading{Style.RESET_ALL} {file['name']} ...")
    done = False
    rate_limit_count = 0
    while done is False and rate_limit_count < 20:
        try:
            status, done = downloader.next_chunk()
        except Exception as ex:
            if "exportsizelimitexceeded" in str(ex).lower():
                print(f"{Fore.YELLOW}Export too large for file{Style.RESET_ALL} {file['name']} ... attempting direct download")
                file_info = service.files().get(fileId=file['id'], supportsAllDrives=True, acknowledgeAbuse=abuse, fields='exportLinks').execute()
                url = file_info['exportLinks'][resolved_mime_type]
                http = google_auth_httplib2.AuthorizedHttp(service._http.credentials)
                dlfile = HttpRequest(http, HttpRequest.null_postproc, url)
                downloader = MediaIoBaseDownload(fh, dlfile, chunksize=CHUNK_SIZE)
                rate_limit_count -= 1
            if "abuse" in str(ex).lower():
                if not noiter: print()
                print(f"{Fore.RED}Abuse error for file{Style.RESET_ALL} {file['name']} ...")
                rate_limit_count = 21
            print(file['id'])
            DEBUG_STATEMENTS.append(f'File Name: {file["name"]}, File ID: {file["id"]}, Exception: {ex}')
            rate_limit_count += 1
    fh.close()
    if noiter and rate_limit_count == 20: print(f"{Fore.RED}Error      {Style.RESET_ALL} {file['name']} ...")
    os.makedirs(destination, exist_ok=True)
    while True:
        try:
            shutil.move(os.path.join('buffer', rand_id), os.path.join(destination, file['name']))
            break
        except PermissionError:
            # wait out the file write before attempting to move
            pass
    return rate_limit_count

def get_folder_id(link):
    # function to isolate folder id
    if 'drive.google.com' in link:
        link = link.split('/view')[0].split('/edit')[0] # extensions to file names
        link = link.rsplit('/', 1)[-1] # final backslash
        link = link.split('?usp')[0] # ignore usp=sharing and usp=edit
        # open?id=
        link = link.rsplit('open?id=')[-1] # only take what is after open?id=
        return link
    else:
        return link

def save_default_path(path):
    if os.path.isfile('config.json'):
        with open('config.json', 'r') as f:
            config = json.load(f)
        config['default_path'] = path
    else:
        config = {}
        config['default_path'] = path
    with open('config.json', 'w') as f:
        f.write(json.dumps(config, indent= 4))

def get_download_status(rlc, start):
    if rlc == -1: # skipped file
        status = f'{Fore.CYAN}Skipped:   {Style.RESET_ALL} '
    elif rlc == 0:
        status = f'{Fore.GREEN}Downloaded:{Style.RESET_ALL} '
    elif rlc < 20:
        status = f'{Fore.YELLOW}Warning:   {Style.RESET_ALL} '
    else:
        status = f'{Fore.RED}Error:     {Style.RESET_ALL} '
    time_req = str(int(time.time() - start)) + 's'
    main_str = f'{Fore.BLUE}[Time: {time_req.rjust(5)}]{Style.RESET_ALL}'
    end_str = ''
    if rlc > 0 and rlc < 20:
        end_str += f' [Rate Limit Count: {rlc}] File saved'
    elif rlc >= 20:
        end_str += f' [Rate Limit Count: {rlc}] Partial file saved'
    return (status, main_str, end_str)
