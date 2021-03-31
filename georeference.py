#!/usr/bin/env python
"""
Script to georeference Nikon D800 images using a GPX track.

Default arguments (filepaths) may have to be edited in the main() function.

REQUIREMENT: Needs to be run on Linux right now and have exiftool installed.
"""

import datetime
import os
import subprocess

import pandas as pd

import gpxpy

# What suffix to relate raw images to
RAW_SUFFIX = "NEF"
OTHER_SUFFIXES = ["jpg", "JPG", "jpeg", "tiff", "tif"]


def check_if_valid_filename(filename: str):
    """Check if a filename corresponds to an image file."""
    if len(filename.split(".")) < 2:  # Equals true if file does not have a suffix, e.g.: "file", as opposed to "file.jpg"
        return False
    suffix = filename.split(".")[-1]
    # Check if the suffix is in the list of valid suffixes
    if suffix not in [RAW_SUFFIX] + OTHER_SUFFIXES:
        return False

    return True  # If it is valid


def get_cam_times(directory: str) -> pd.Series:
    """Get the EXIF capture time from each raw image in a directory as a Pandas Series."""

    # Create an empty Pandas series with datetime as its data type
    # Index is image filename, date taken is data
    cam_times = pd.Series(dtype="datetime64[ns]")

    files = os.listdir(directory)  # Get list of all files in the directory
    for i, file in enumerate(files):  # Loop over all files
        # Check if valid image file
        if not check_if_valid_filename(file):
            continue

        # For every 50 images, print a progress update (process may take a while)
        if i % 50 == 0 and i != 0:
            print(f"File {i} / {len(files)}")

        # Get the full path ("image_dir/image.jpg")
        full_path = os.path.join(directory, file)
        # Use exiftool in a shell environment, filter out the "Create Date" and take the last entry (there are duplicates of the same one)
        exiftool_output = subprocess.check_output(f"exiftool {full_path} | grep 'Create Date' | tail -1", shell=True)
        # Do some string magic to extract only the date and time from the output
        date = exiftool_output.decode("utf-8").split(" : ")[1].strip()

        # Convert to a DateTime object and add to the series
        cam_times[file] = pd.to_datetime(date, format="%Y:%m:%d %H:%M:%S.%f")
    return cam_times


def get_time_diff(photo_sync_directory: str, gps_time_file: str) -> datetime.datetime:
    """Get the time difference between the GPS time and the camera's internal time by comparing photographs of waypoints."""

    # Create empty Pandas Dataframe
    times = pd.DataFrame(columns=["cam", "gps"], dtype="datetime64")

    # Get the times from the camera and add them to the dataframe
    cam_times = get_cam_times(photo_sync_directory)
    times.loc[:, "cam"] = cam_times

    # Open the GPS time file and add the times to the dataframe
    # It is structured as: *picture filename*,*equivalent gps time*
    with open(gps_time_file) as file:
        for line in file.readlines():
            cam, gps_time = line.split(",")
            times.loc[cam, "gps"] = pd.to_datetime(gps_time, format="%Y-%m-%d %H:%M:%S")

    # Get the time differences
    # Type correction (.astype) may not be neccessary anymore.
    times["diff"] = times["cam"] - times["gps"].astype("datetime64")

    # Get the mean time offset
    diff = times["diff"].mean()

    # Round the diff to nearest 1/10th of a second
    # The Nikon camera data is only shown to 1/10th of a second.
    offset = round(diff.microseconds / 1e5) * int(1e5) - diff.microseconds
    diff += pd.Timedelta(microseconds=offset)

    return diff


def read_gpx(gpx_file: str) -> pd.DataFrame:
    """Read a GPX file and return a Pandas Dataframe."""

    # Create empty Pandas dataframe
    coords = pd.DataFrame(columns=["lon", "lat", "elev"])
    # Open the GPX file and get every track and segment within it
    with open(gpx_file) as file:
        gpx = gpxpy.parse(file)

        for track in gpx.tracks:
            for segment in track.segments:
                for point in segment.points:
                    # Put each point in the coords dataframe
                    coords.loc[pd.to_datetime(point.time)] = point.longitude, point.latitude, point.elevation

    # Convert index from regular datetime to nanosecond datetime
    coords.index = coords.index.astype("datetime64[ns]")
    return coords


def georeference(coords: pd.DataFrame, destination_folder: str):
    """Use the GNSS coords file to apply the georeferencing to images in a folder."""

    # Get every coordinate with a valid image related to it.
    cam_coords = coords.dropna()

    for file in os.listdir(destination_folder):
        # Check if valid image file
        if not check_if_valid_filename(file):
            continue

        # Get full path of image
        full_path = os.path.join(destination_folder, file)

        suffix = file.split(".")[-1]
        # Loop through all image coordinates and try to match the current file with an entry
        for i, coord in cam_coords.iterrows():
            if file.replace(f".{suffix}", "") in coord["photo"]:  # If the filename (minus the suffix) matches
                cam_coords.drop(i, inplace=True)  # Then remove it from the dataframe (to speed up the next loop)
                break  # And break the loop, thus preserving the 'coord' variable to use further down

        # Use exiftool to write the location data
        os.system(
            f"exiftool -EXIF:GPSLongitude='{coord.lon}' -EXIF:GPSLatitude='{coord.lat}' -EXIF:GPSAltitude='{coord.elev}' -GPSLongitudeRef='East' -GPSLatitudeRef='North' -overwrite_original {full_path}")


def main(photo_sync_directory: str = "ClockSync/", gps_time_file: str = "ClockSync/gps_times.csv", destination_folder: str = "TIF", gpx_file: str = "2020-07-15 145632.gpx", csv_table_out: str = "camera_coordinates.csv"):
    """Run all functions in the correct order to georeference exported images."""
    print("Calculating clock difference")
    time_diff = get_time_diff(photo_sync_directory, gps_time_file)

    print("Getting camera timing metadata")
    destination_cam_times = get_cam_times(destination_folder)
    # Switch the index and data values with each other
    destination_cam_times = pd.Series(data=destination_cam_times.index.values, index=destination_cam_times.values)

    # Subtract the time difference between the camera and GNSS to sync them
    destination_cam_times.index -= time_diff

    # Read the GPX coordinates and interpolate them to 1/10th second (10 Hz) temporal resolution
    print("Loading GPX file")
    coords = read_gpx(gpx_file).resample("100L").interpolate()

    coords["photo"] = destination_cam_times  # Add a column for where the coordinates have a corresponding coordinate

    # Export the coordinates with an associated image
    coords.dropna().to_csv(csv_table_out)

    # Finally, georeference the destination folder's images
    print("Georeferencing images")
    georeference(coords, destination_folder)


if __name__ == "__main__":
    main()
