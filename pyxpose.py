# -*- coding: utf-8 -*-
"""pyxpose.

Usage:
  pyxpose.py <gallery-path>
  pyxpose.py <gallery-path> --template=<template-path>
  pyxpose.py (-h | --help)

Options:
  --template=<template-path>    Define another template [default: ./template/]
  -h --help                     Show this screen.

"""

from docopt import docopt

import os
import sys
import glob
import markdown
import jinja2
import time
import numpy as np
import scipy
import scipy.misc
import scipy.cluster
import math
import shutil
import natsort
from tqdm import tqdm
from PIL import Image
from PIL.ExifTags import TAGS

class BadCaptionError(Exception): pass
class NoExposureDataError(Exception): pass


def hsv2rgb(h, s, v):
    # rgb to hsv and the other way around
    # http://code.activestate.com/recipes/576919-python-rgb-and-hsv-conversion/
    h = float(h)
    s = float(s)
    v = float(v)
    h60 = h / 60.0
    h60f = math.floor(h60)
    hi = int(h60f) % 6
    f = h60 - h60f
    p = v * (1 - s)
    q = v * (1 - f * s)
    t = v * (1 - (1 - f) * s)
    r, g, b = 0, 0, 0
    if hi == 0: r, g, b = v, t, p
    elif hi == 1: r, g, b = q, v, p
    elif hi == 2: r, g, b = p, v, t
    elif hi == 3: r, g, b = p, q, v
    elif hi == 4: r, g, b = t, p, v
    elif hi == 5: r, g, b = v, p, q
    r, g, b = int(r * 255), int(g * 255), int(b * 255)
    return r, g, b


def rgb2hsv(r, g, b):
    # rgb to hsv and the other way around
    # http://code.activestate.com/recipes/576919-python-rgb-and-hsv-conversion/
    r, g, b = r/255.0, g/255.0, b/255.0
    mx = max(r, g, b)
    mn = min(r, g, b)
    df = mx-mn
    if mx == mn:
        h = 0
    elif mx == r:
        h = (60 * ((g-b)/df) + 360) % 360
    elif mx == g:
        h = (60 * ((b-r)/df) + 120) % 360
    elif mx == b:
        h = (60 * ((r-g)/df) + 240) % 360
    if mx == 0:
        s = 0
    else:
        s = df/mx
    v = mx
    return h, s, v


def find_a_dominant_color(image):
    # K-mean clustering to find the k most dominant color, from:
    # http://stackoverflow.com/questions/3241929/python-find-dominant-most-common-color-in-an-image
    n_clusters = 5

    # Get image into a workable form
    im = image.copy()
    im = im.resize((150, 150))      # optional, to reduce time
    ar = scipy.misc.fromimage(im)
    im_shape = ar.shape
    ar = ar.reshape(scipy.product(im_shape[:2]), im_shape[2])
    ar = np.float_(ar)

    # Compute clusters
    codes, dist = scipy.cluster.vq.kmeans(ar, n_clusters)
    vecs, dist = scipy.cluster.vq.vq(ar, codes)         # assign codes
    counts, bins = scipy.histogram(vecs, len(codes))    # count occurrences

    # Get the indexes of the most frequent, 2nd most frequent, 3rd, ...
    sorted_idxs = np.argsort(counts)

    # Get the color
    peak = codes[sorted_idxs[1]] # get second most frequent color

    return [int(i) for i in peak.tolist()] # list comprehension to quickly cast everything to int


def process_caption(caption_raw, markdown_object):
    caption = md.convert(caption_raw)
    meta = markdown_object.Meta
    md.reset()  # reset markdown object for further sage
    return caption, meta


def process_exif(exif,photo_metadata):
    # Get basic EXIF info
    photo_metadata['exposure_data'] = dict()
    available_exif = exif.keys()
    if 'FNumber' in available_exif:
        photo_metadata['exposure_data']['aperture'] = str(exif['FNumber'][0]/exif['FNumber'][1])
    if 'ExposureTime' in available_exif:
        photo_metadata['exposure_data']['exposure_time'] = str(exif['ExposureTime'][0])+'/'+str(exif['ExposureTime'][1])
    if 'ISOSpeedRatings' in available_exif:
        photo_metadata['exposure_data']['iso'] = str(exif['ISOSpeedRatings'])
    if 'FocalLengthIn35mmFilm' in available_exif:
        photo_metadata['exposure_data']['focal_length'] = str(exif['FocalLengthIn35mmFilm'])
    if 'LensMake' in available_exif:
        photo_metadata['exposure_data']['lens_maker'] = str(exif['LensMake'])
    if 'LensModel' in available_exif:
        photo_metadata['exposure_data']['lens_model'] = str(exif['LensModel'])
    if 'Make' in available_exif:
        photo_metadata['exposure_data']['camera_maker'] = str(exif['Make'])
    if 'Model' in available_exif:
        photo_metadata['exposure_data']['camera_model'] = str(exif['Model'])

    # Get caption
    if 'ImageDescription' in exif.keys():
        # for some reason lightroom encode its exif comment un latin-1
        caption_raw = bytes(exif['ImageDescription'], 'latin-1').decode('utf-8')

        #decode the md within the caption
        photo_metadata['caption'], photo_metadata['caption_meta'] = process_caption(caption_raw, md)
        if 'style' in photo_metadata['caption_meta'].keys():
            photo_metadata['caption_meta']['style'] = ''.join(photo_metadata['caption_meta']['style'])
        if 'class' in photo_metadata['caption_meta'].keys():
            photo_metadata['caption_meta']['class'] = ' '.join(photo_metadata['caption_meta']['class'])
        try:
            photo_metadata['caption_meta']['has_caption_position'] = True
            photo_metadata['caption_meta']['top'] = photo_metadata['caption_meta']['top'][0]
            photo_metadata['caption_meta']['left'] = photo_metadata['caption_meta']['left'][0]
            photo_metadata['caption_meta']['width'] = photo_metadata['caption_meta']['width'][0]
            photo_metadata['caption_meta']['height'] = photo_metadata['caption_meta']['height'][0]
        except KeyError:
            photo_metadata['caption_meta']['has_caption_position'] = False
            photo_metadata['caption_meta']['top'] = 80
            photo_metadata['caption_meta']['left'] = 60
            photo_metadata['caption_meta']['width'] = 40
            photo_metadata['caption_meta']['height'] = 20
            raise BadCaptionError()
    else:
         photo_metadata['caption'] = ''
         photo_metadata['caption_meta'] = ''

    #END process_exif


def create_thumbnails(image, filename):
    # === Create thumbnails ===
    stripped_filename = filename.replace(' ', '')
    thumb = image.copy()
    resolutions = [1920, 1280, 1024, 640, 320]
    for res in resolutions:
        thumb.thumbnail((res, res), resample=Image.ANTIALIAS)
        if 'exif' in im.info.keys():
            thumb.save('./img/'+stripped_filename + '-'+str(res)+'.jpg', format="jpeg", exif=im.info['exif'], quality=80)
        else:
            thumb.save('./img/'+stripped_filename + '-'+str(res)+'.jpg', format="jpeg", quality=80)


def get_exif(image):
    # EXIF extraction inspired by http://stackoverflow.com/questions/765396/exif-manipulation-library-for-python
    exif = dict()
    info = image._getexif()
    if info is not None:
        for tag, value in info.items():
            decoded = TAGS.get(tag, tag)
            exif[decoded] = value
    else:
        raise NoEXIFError

    return exif


def process_gallery_description(input_file_gallery_description):
    sidebar_content = md.convert(input_file_gallery_description.read())
    if sidebar_content:
        sidebar_meta = md.Meta
        if 'title' in sidebar_meta.keys():
            gallery_title = sidebar_meta['title'][0]
        else:
            gallery_title = 'Photo gallery'
            print('Warning: Gallery title not found, using default value')
        if 'short-description' in sidebar_meta.keys():
            gallery_description = sidebar_meta['short-description'][0]
        else:
            gallery_description = ''
            print('Warning: Gallery description not found, using default value')
    else:
        gallery_title = 'Photo gallery'
        print('Warning: Gallery title not found, using default value')
        gallery_description = ''
        print('Warning: Gallery description not found, using default value')
        sidebar_content = ''

    md.reset()
    return sidebar_content, gallery_title, gallery_description

if __name__ == '__main__':
    arguments = docopt(__doc__)
    DATADIR = arguments['<gallery-path>']
    TEMPLATE_DIR = arguments['--template']
    try:
        os.chdir(TEMPLATE_DIR)
        TEMPLATE_DIR = os.getcwd()
    except OSError:
        print('/!\\ Error: Template path cannot be found.')
        print('I tried this path: ' + str(TEMPLATE_DIR))
        sys.exit()
    try:
        os.chdir(DATADIR)
        DATADIR = os.getcwd()
    except OSError:
        print('/!\\ Error: Gallery path cannot be found.')
        print('I tried this path: ' + str(DATADIR))
        sys.exit()

    t = time.time()
    md = markdown.Markdown(extensions=['markdown.extensions.meta','markdown.extensions.extra'])
    file_list_jpg = glob.glob('*.jpg')
    file_list_md = glob.glob('*.md')
    file_list_txt = glob.glob('*.txt')
    file_list = file_list_jpg + file_list_txt + file_list_md
    if not os.path.exists('./img/'):
        os.makedirs('./img/')
    if not os.path.exists('./fonts/'):
        os.makedirs('./fonts/')
    # Exclude thumbnails
    file_list = [fn for fn in file_list if not os.path.basename(fn).endswith('-1920.jpg')]
    file_list = [fn for fn in file_list if not os.path.basename(fn).endswith('-1280.jpg')]
    file_list = [fn for fn in file_list if not os.path.basename(fn).endswith('-1024.jpg')]
    file_list = [fn for fn in file_list if not os.path.basename(fn).endswith('-640.jpg')]
    file_list = [fn for fn in file_list if not os.path.basename(fn).endswith('-320.jpg')]

    # Exclude gallery file
    file_list = [fn for fn in file_list if not os.path.basename(fn).endswith('gallery-description.txt')]
    file_list = [fn for fn in file_list if not os.path.basename(fn).endswith('gallery-description.md')]
    file_list = [fn for fn in file_list if not os.path.basename(fn).endswith('_gallery-description.txt')]
    file_list = [fn for fn in file_list if not os.path.basename(fn).endswith('_gallery-description.md')]

    # Exclude files beginning with '_'
    file_list = [fn for fn in file_list if not os.path.basename(fn).startswith('_')]

    gallery_title = 'Photo gallery'
    gallery_description = ''
    sidebar_content = ''
    # Load sidebar file
    if os.path.isfile('gallery-description.txt'):
        input_file_gallery_description = open('gallery-description.txt', 'r')
        sidebar_content, gallery_title, gallery_description = process_gallery_description(input_file_gallery_description)
    elif os.path.isfile('gallery-description.md'):
        input_file_gallery_description = open('gallery-description.md', 'r')
        sidebar_content, gallery_title, gallery_description = process_gallery_description(input_file_gallery_description)
    else:
        print('Warning: "gallery-description.(txt|md)"  not found, using default gallery name and description')

    slides = []
    for filename in tqdm(natsort.natsorted(file_list)):
        # Get type of the slide: text or photo ?
        # Text
        if os.path.splitext(filename)[1] == '.txt' or os.path.splitext(filename)[1] == '.md':
            input_file = open(filename, mode="r",encoding='utf-8')
            slide_md = md.convert(input_file.read())
            slide = {'type': 'text', 'data': slide_md}
            slides.append(slide)
            pass
        # Photo
        elif os.path.splitext(filename)[1] == '.jpg':
            im = Image.open(filename)

            ## Process photo metadata
            photo_metadata = dict()

            # Add filename
            photo_metadata['filename'] = os.path.splitext(filename)[0]

            # Add dominant color
            photo_metadata['color'] = find_a_dominant_color(im)
            # Add contrasting color
            h, s, v = rgb2hsv(photo_metadata['color'][0], photo_metadata['color'][1], photo_metadata['color'][2])
            v = v + 0.6 % 1 # Add 30 and modulo 100 to the luminance/value
            r,g,b = hsv2rgb(h, s, v)
            photo_metadata['complement_color'] = [r, g, b]

            # Process image metadata (EXIF, caption...)
            try:
                exif = get_exif(im)
                process_exif(exif, photo_metadata)  # variable are passed by assignment, photo_metadata will be updated
            except BadCaptionError:
                print('Warning: Missing or malformed caption position in file ' + str(filename))
                print('Using default position')
            except NoExposureDataError:
                print('Warning: Missing EXIF found in file: '+ filename)

            # Create thumbnails
            create_thumbnails(im, str(photo_metadata['filename']))

            # Strip whitespace from the filename
            photo_metadata['filename'] = photo_metadata['filename'].replace(' ', '')

            # Append to list
            slide = {'type': 'photo', 'data': photo_metadata}
            if filename.endswith('_private.jpg'):
                slide['is_public'] = False
            else:
                slide['is_public'] = True

            slides.append(slide)



    templateLoader = jinja2.FileSystemLoader(TEMPLATE_DIR) # where the template file is

    def isPublic(s):
        if 'is_public' in s.keys():
            return s['is_public']
        else:
            raise ValueError

    templateEnv = jinja2.Environment(loader=templateLoader)
    TEMPLATE_FILE = "pyxpose_template.html"
    template = templateEnv.get_template(TEMPLATE_FILE)

    templateVars_public = {'gallery_title': gallery_title,
                    'gallery_description': gallery_description,
                    'slides': slides,
                    'sidebar': sidebar_content}
    outputText_public = template.render(templateVars_public)
    print('Galleries saved to: ' + os.getcwd())
    ff = open('gallery.html', 'w', encoding='utf-8')
    ff.write(outputText_public)

    slides_without_private = [slide for slide in slides if isPublic(slide)]
    if not len(slides_without_private) == len(slides):
        templateVars_private = {'gallery_title': gallery_title,
                        'gallery_description': gallery_description,
                        'slides': slides_without_private,
                        'sidebar': sidebar_content}
        outputText_private = template.render(templateVars_private)
        ff = open('gallery_private.html', 'w', encoding='utf-8')
        ff.write(outputText_private)


    shutil.copy(TEMPLATE_DIR + "/style.css", DATADIR)
    if not os.path.exists(DATADIR+'/fonts/'):
        os.makedirs(DATADIR+'/fonts/')
    shutil.copy(TEMPLATE_DIR+'/fonts/icomoon.eot', DATADIR+'/fonts/')
    shutil.copy(TEMPLATE_DIR+'/fonts/icomoon.svg', DATADIR+'/fonts/')
    shutil.copy(TEMPLATE_DIR+'/fonts/icomoon.ttf', DATADIR+'/fonts/')
    shutil.copy(TEMPLATE_DIR+'/fonts/icomoon.woff', DATADIR+'/fonts/')
    elapsed = time.time() - t
    print('Finished in '+str(elapsed)+' seconds')