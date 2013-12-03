# -*- coding: utf-8 -*-
"""
Created on Thu Oct 31 22:00:39 2013

@author: Vaclav Petras <wenzeslaus gmail.com>
"""

import os
import tempfile

from grass.script import core as gcore
from grass.script import setup as gsetup
from grass.pygrass.gis import Mapset, Location

from routleaflet.utils import get_region, set_region, \
    get_location_proj_string, reproject_region


def map_extent_to_js_leaflet_list(extent):
    """extent dictionary with latitudes and longitudes extent
    (east, north, west, south)
    """
    return "[[{south}, {east}], [{north}, {west}]]".format(**extent)


def map_extent_to_file_content(extent):
    """extent dictionary with latitudes and longitudes extent
    (east, north, west, south)
    """
    return "{east} {north}\n{west} {south}".format(**extent)


def get_map_extent_for_file(file_name):
    wgs84_file = open(file_name, 'r')
    enws = wgs84_file.readlines()
    elon, nlat = enws[0].strip().split(' ')
    wlon, slat = enws[1].strip().split(' ')
    return {'east': elon, 'north': nlat,
            'west': wlon, 'south': slat}


def proj_to_wgs84(region):
    proj_in = '{east} {north}\n{west} {south}'.format(**region)
    proc = gcore.start_command('m.proj', input='-', separator=' , ',
                               flags='od',
                               stdin=gcore.PIPE, stdout=gcore.PIPE,
                               stderr=gcore.PIPE)
    proc.stdin.write(proj_in)
    proc.stdin.close()
    proc.stdin = None
    proj_out, errors = proc.communicate()
    if proc.returncode:
        raise RuntimeError("m.proj error: %s" % errors)
    enws = proj_out.split(os.linesep)
    elon, nlat, unused = enws[0].split(' ')
    wlon, slat, unused = enws[1].split(' ')
    return {'east': elon, 'north': nlat,
            'west': wlon, 'south': slat}


def get_map_extent_for_location(map_name):
    info_out = gcore.read_command('r.info', map=map_name, flags='g')
    info = gcore.parse_key_val(info_out, sep='=')
    return proj_to_wgs84(info)

    # pygrass code which does not work on ms windows
    #mproj = Module('m.proj')
    #mproj.inputs.stdin = proj_in
    #mproj(flags='o', input='-', stdin_=subprocess.PIPE,
    #      stdout_=subprocess.PIPE)
    #print mproj.outputs.stdout


# TODO: support parallel calls, rewrite as class?


def export_png_in_projection(src_mapset_name, map_name, output_file,
                             epsg_code,
                             routpng_flags, compression, wgs84_file,
                             use_region=True):
    """

    :param use_region: use computation region and not map extent
    """
    if use_region:
        src_region = get_region()
        src_proj_string = get_location_proj_string()

    # TODO: change only location and not gisdbase?
    # we rely on the tmp dir having enough space for our map
    tgt_gisdbase = tempfile.mkdtemp()
    # this is not needed if we use mkdtemp but why not
    tgt_location = 'r.out.png.proj_location_%s' % epsg_code
    # because we are using PERMANENT we don't have to create mapset explicitly
    tgt_mapset_name = 'PERMANENT'

    src_mapset = Mapset(src_mapset_name)

    # get source (old) and set target (new) GISRC enviromental variable
    # TODO: set environ only for child processes could be enough and it would
    # enable (?) parallel runs
    src_gisrc = os.environ['GISRC']
    tgt_gisrc = gsetup.write_gisrc(tgt_gisdbase,
                                   tgt_location, tgt_mapset_name)
    os.environ['GISRC'] = tgt_gisrc
    if os.environ.get('WIND_OVERRIDE'):
        old_temp_region = os.environ['WIND_OVERRIDE']
        del os.environ['WIND_OVERRIDE']
    else:
        old_temp_region = None
    # these lines looks good but anyway when developing the module
    # switching location seemed fragile and on some errors (while running
    # unfinished module) location was switched in the command line

    try:
        # the function itself is not safe for other (backgroud) processes
        # (e.g. GUI), however we already switched GISRC for us
        # and child processes, so we don't influece others
        gcore.create_location(dbase=tgt_gisdbase,
                              location=tgt_location,
                              epsg=epsg_code,
                              datum=None,
                              datum_trans=None)

        # Mapset object cannot be created if the real mapset does not exists
        tgt_mapset = Mapset(gisdbase=tgt_gisdbase, location=tgt_location,
                            mapset=tgt_mapset_name)
        # set the current mapset in the library
        # we actually don't need to switch when only calling modules
        # (right GISRC is enough for them)
        tgt_mapset.current()

        # setting region
        if use_region:
            # respecting computation region of the src location
            # by previous use g.region in src location
            # and m.proj and g.region now
            # respecting MASK of the src location would be hard
            # null values in map are usually enough
            tgt_proj_string = get_location_proj_string()
            tgt_region = reproject_region(src_region,
                                          from_proj=src_proj_string,
                                          to_proj=tgt_proj_string)
            # uses g.region thus and sets region only for child processes
            # which is enough now
            set_region(tgt_region)
        else:
            # find out map extent to import everything
            # using only classic API because of some problems with pygrass
            # on ms windows
            rproj_out = gcore.read_command('r.proj', input=map_name,
                                           dbase=src_mapset.gisdbase,
                                           location=src_mapset.location,
                                           mapset=src_mapset.name,
                                           output=map_name, flags='g')
            a = gcore.parse_key_val(rproj_out, sep='=', vsep=' ')
            gcore.run_command('g.region', **a)

        # map import
        gcore.run_command('r.proj', input=map_name, dbase=src_mapset.gisdbase,
                          location=src_mapset.location, mapset=src_mapset.name,
                          output=map_name)

        # actual export
        gcore.run_command('r.out.png', input=map_name, output=output_file,
                          compression=compression, flags=routpng_flags)

        # outputting file with WGS84 coordinates
        if wgs84_file:
            gcore.message("Projecting coordinates to LL WGS 84...")
            with open(wgs84_file, 'w') as data_file:
                if use_region:
                    # map which is smaller than region is imported in its own
                    # small extent, but we export image in region, so we need
                    # bounds to be for region, not map
                    # hopefully this is consistent with r.out.png behavior
                    data_file.write(
                        map_extent_to_file_content(
                            proj_to_wgs84(get_region())) + '\n')
                else:
                    # use map to get extent
                    # the result is actually the same as using map
                    # if region is the same as map (use_region == False)
                    data_file.write(
                        map_extent_to_file_content(
                            get_map_extent_for_location(map_name))
                        + '\n')

    finally:
        # juts in case we need to do something in the old location
        # our callers probably do
        os.environ['GISRC'] = src_gisrc
        if old_temp_region:
            os.environ['WIND_OVERRIDE'] = old_temp_region
        # set current in library
        src_mapset.current()

        # delete the whole gisdbase
        # delete file by file to ensure that we are deleting only our things
        # exception will be raised when removing non-empty directory
        tgt_location_path = Location(gisdbase=tgt_gisdbase,
                                     location=tgt_location).path()
        tgt_mapset.delete()
        os.rmdir(tgt_location_path)
        # dir created by tempfile.mkdtemp() needs to be romved manually
        os.rmdir(tgt_gisdbase)
        # we have to remove file created by tempfile.mkstemp function
        # in write_gisrc function
        os.remove(tgt_gisrc)
