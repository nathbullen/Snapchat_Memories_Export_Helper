import json
import os
import zipfile
import requests
from datetime import datetime
from pathlib import Path
import platform
import subprocess
import shutil

# For setting file timestamps
import time

# For setting EXIF metadata (images/videos)
try:
    from PIL import Image
    from PIL.ExifTags import TAGS, GPSTAGS
    import piexif
    HAS_PIEXIF = True
except ImportError:
    HAS_PIEXIF = False
    print("Warning: piexif not installed. Install with: pip install piexif pillow")

def parse_date(date_str):
    """Parse date string from JSON format to datetime object."""
    # Format: "2025-05-18 01:17:50 UTC"
    return datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S UTC")

def parse_location(location_str):
    """Parse location string to get latitude and longitude."""
    # Format: "Latitude, Longitude: 39.734604, -104.98702"
    if not location_str or location_str == "N/A":
        return None, None
    
    try:
        coords = location_str.split(": ")[1]
        lat, lon = coords.split(", ")
        return float(lat), float(lon)
    except:
        return None, None

def decimal_to_dms(decimal):
    """Convert decimal degrees to degrees, minutes, seconds format for EXIF."""
    is_positive = decimal >= 0
    decimal = abs(decimal)
    
    degrees = int(decimal)
    minutes = int((decimal - degrees) * 60)
    seconds = ((decimal - degrees) * 60 - minutes) * 60
    
    return ((degrees, 1), (minutes, 1), (int(seconds * 100), 100))

def set_image_exif_metadata(file_path, date_obj, latitude, longitude):
    """Set EXIF metadata for image files."""
    if not HAS_PIEXIF:
        print(f"  Skipping EXIF metadata (piexif not installed)")
        return
    
    try:
        # Open image to verify it's valid
        try:
            img = Image.open(file_path)
            img_format = img.format
            img.close()
        except Exception as img_error:
            print(f"  Skipping EXIF metadata (invalid/corrupted image file)")
            return
        
        # Only process JPEG files
        if img_format not in ['JPEG', 'JPG']:
            print(f"  Skipping EXIF metadata (format: {img_format}, only JPEG supported)")
            return
        
        # Load existing EXIF data or create new
        try:
            exif_dict = piexif.load(file_path)
        except:
            exif_dict = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}
        
        # Set date/time (must be bytes)
        date_str = date_obj.strftime("%Y:%m:%d %H:%M:%S").encode('ascii')
        exif_dict["Exif"][piexif.ExifIFD.DateTimeOriginal] = date_str
        exif_dict["Exif"][piexif.ExifIFD.DateTimeDigitized] = date_str
        exif_dict["0th"][piexif.ImageIFD.DateTime] = date_str
        
        # Set GPS data if available
        if latitude is not None and longitude is not None:
            lat_dms = decimal_to_dms(latitude)
            lon_dms = decimal_to_dms(longitude)
            
            exif_dict["GPS"][piexif.GPSIFD.GPSLatitude] = lat_dms
            exif_dict["GPS"][piexif.GPSIFD.GPSLatitudeRef] = b"N" if latitude >= 0 else b"S"
            exif_dict["GPS"][piexif.GPSIFD.GPSLongitude] = lon_dms
            exif_dict["GPS"][piexif.GPSIFD.GPSLongitudeRef] = b"E" if longitude >= 0 else b"W"
        
        # Save EXIF data using PIL to avoid corruption
        exif_bytes = piexif.dump(exif_dict)
        
        # Re-save the image with EXIF data
        img = Image.open(file_path)
        img.save(file_path, "JPEG", exif=exif_bytes, quality=95)
        img.close()
        
        print(f"  ✓ Set EXIF metadata (date + GPS)")
    except Exception as e:
        print(f"  Warning: Could not set EXIF metadata: {e}")

def check_ffmpeg():
    """Check if ffmpeg is available on the system."""
    return shutil.which('ffmpeg') is not None

def set_video_metadata(file_path, date_obj, latitude, longitude):
    """Set metadata for video files using ffmpeg."""
    if not check_ffmpeg():
        print(f"  Skipping video metadata (ffmpeg not found)")
        return False
    
    try:
        # Create a temporary output file
        temp_path = str(file_path) + ".temp.mp4"
        
        # Prepare metadata arguments
        metadata_args = [
            'ffmpeg',
            '-i', str(file_path),
            '-c', 'copy',  # Copy streams without re-encoding
            '-map_metadata', '0',  # Copy existing metadata
        ]
        
        # Add creation time metadata
        creation_time = date_obj.strftime("%Y-%m-%dT%H:%M:%S.000000Z")
        metadata_args.extend(['-metadata', f'creation_time={creation_time}'])
        metadata_args.extend(['-metadata', f'date={date_obj.strftime("%Y")}'])
        
        # Add GPS location metadata if available
        if latitude is not None and longitude is not None:
            # ISO 6709 format for location
            location_str = f"{latitude:+.6f}{longitude:+.6f}/"
            metadata_args.extend(['-metadata', f'location={location_str}'])
            metadata_args.extend(['-metadata', f'location-eng={location_str}'])
            metadata_args.extend(['-metadata', f'com.apple.quicktime.location.ISO6709={location_str}'])
        
        # Output file
        metadata_args.extend(['-y', temp_path])  # -y to overwrite without asking
        
        # Run ffmpeg
        result = subprocess.run(
            metadata_args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if platform.system() == 'Windows' else 0
        )
        
        if result.returncode == 0:
            # Replace original file with the one that has metadata
            os.replace(temp_path, file_path)
            print(f"  ✓ Set video metadata (date + GPS) with ffmpeg")
            return True
        else:
            print(f"  Warning: ffmpeg failed: {result.stderr.decode()[:100]}")
            if os.path.exists(temp_path):
                os.remove(temp_path)
            return False
            
    except Exception as e:
        print(f"  Warning: Could not set video metadata: {e}")
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except:
                pass
        return False

def set_file_timestamps(file_path, date_obj):
    """Set file modification and access times."""
    timestamp = date_obj.timestamp()
    
    try:
        os.utime(file_path, (timestamp, timestamp))
        print(f"  ✓ Set file timestamp to {date_obj}")
    except Exception as e:
        print(f"  Warning: Could not set file timestamp: {e}")

def download_media(url, output_path):
    """Download media file from URL, handling zips automatically."""
    output_path = Path(output_path)
    try:
        response = requests.get(url, stream=True, timeout=30)
        response.raise_for_status()

        # 1. Download to a temp file first
        temp_path = output_path.with_suffix(output_path.suffix + ".tmp")
        
        with open(temp_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        # 2. Check for ZIP signature (Magic Number 50 4B 03 04)
        is_zip = False
        with open(temp_path, 'rb') as f:
            header = f.read(4)
            if header == b'\x50\x4b\x03\x04':
                is_zip = True

        if is_zip:
            print(f"  ⚠ Detected ZIP file (Story/Bundle). Unzipping...")
            
            # Create a temp folder to unzip into
            extract_folder = output_path.parent / "temp_unzip_folder"
            if extract_folder.exists():
                shutil.rmtree(extract_folder)
            extract_folder.mkdir()
            
            try:
                with zipfile.ZipFile(temp_path, 'r') as zip_ref:
                    zip_ref.extractall(extract_folder)

                # First, look for video files (MP4)
                candidates = list(extract_folder.glob('*.mp4'))
                
                # If no videos, look for images (JPG/JPEG)
                if not candidates:
                    candidates = list(extract_folder.glob('*.jpg')) + list(extract_folder.glob('*.jpeg'))

                if candidates:
                    # Pick the largest file found (whether video or image)
                    best_file = max(candidates, key=lambda p: p.stat().st_size)
                    
                    # Move it to the final destination
                    if output_path.exists():
                        output_path.unlink()
                    
                    # If we found an image but the original filename was .mp4, fix the extension
                    if best_file.suffix.lower() in ['.jpg', '.jpeg'] and output_path.suffix.lower() == '.mp4':
                        output_path = output_path.with_suffix(best_file.suffix)
                    
                    shutil.move(str(best_file), str(output_path))
                    print(f"  ✓ Extracted media ({best_file.suffix}) from zip")
                else:
                    print(f"  ⚠ No MP4 or JPG found in zip. Saving as .zip")
                    # ... (rest of the else block remains the same)

                    
            except Exception as e:
                print(f"  ⚠ Error unzipping: {e}")
                # Fallback: just save the original zip
                shutil.move(str(temp_path), str(output_path.with_suffix('.zip')))
                return False
            finally:
                # Cleanup temp folder
                if extract_folder.exists():
                    shutil.rmtree(extract_folder)
                # Cleanup temp file if it still exists
                if temp_path.exists():
                    temp_path.unlink()
                    
        else:
            # Not a zip, just a normal file. Rename temp to final.
            if output_path.exists():
                output_path.unlink()
            shutil.move(str(temp_path), str(output_path))

        return True

    except Exception as e:
        print(f"  Error downloading: {e}")
        # Clean up temp file if it exists
        if 'temp_path' in locals() and temp_path.exists():
            temp_path.unlink()
        return False

def get_file_extension(media_type, content_type=None):
    """Determine file extension based on media type."""
    if media_type == "Image":
        return ".jpg"
    elif media_type == "Video":
        return ".mp4"
    else:
        return ".bin"

def process_json_file(json_path, output_dir="downloads"):
    """Process JSON file and download all media with metadata."""
    # Create output directory
    output_path = Path(output_dir)
    output_path.mkdir(exist_ok=True)
    
    # Load JSON
    print(f"Loading JSON from: {json_path}")
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    # Get media items
    media_items = data.get("Saved Media", [])
    print(f"Found {len(media_items)} media items to download\n")
    
    # Process each item
    for idx, item in enumerate(media_items, 1):
        print(f"[{idx}/{len(media_items)}] Processing...")
        
        # Extract metadata
        date_str = item.get("Date", "")
        media_type = item.get("Media Type", "Unknown")
        location_str = item.get("Location", "")
        download_url = item.get("Media Download Url", "")
        
        if not download_url:
            print("  ⚠ No download URL found, skipping")
            continue
        
        # Parse date and location
        date_obj = parse_date(date_str)
        latitude, longitude = parse_location(location_str)
        
        # Generate filename
        date_formatted = date_obj.strftime("%Y%m%d_%H%M%S")
        extension = get_file_extension(media_type)
        filename = f"{date_formatted}_{idx}{extension}"
        file_path = output_path / filename
        
        print(f"  File: {filename}")
        print(f"  Date: {date_str}")
        if latitude and longitude:
            print(f"  Location: {latitude}, {longitude}")
        
        # Download file
        print(f"  Downloading...", end=" ")
        if download_media(download_url, str(file_path)):
            print("✓ Downloaded")
            
            # Set EXIF metadata for images
            if media_type == "Image" and extension.lower() in ['.jpg', '.jpeg']:
                set_image_exif_metadata(str(file_path), date_obj, latitude, longitude)
            elif media_type == "Video":
                # Set video metadata with ffmpeg
                set_video_metadata(str(file_path), date_obj, latitude, longitude)
            
            # Set file timestamps (do this last to ensure file times reflect original date)
            set_file_timestamps(str(file_path), date_obj)
        
        print()  # Empty line between items
    
    print(f"✓ Complete! Downloaded {len(media_items)} files to '{output_dir}'")

def main():
    """Main function to run the script."""
    print("=== Snapchat Media Downloader with Metadata Preservation ===\n")
    
    # Example: Process a JSON file
    json_file = input("Enter JSON file path: ").strip()
    
    if not json_file:
        json_file = "memories_json/ethan.json"
    
    if not os.path.exists(json_file):
        print(f"Error: File '{json_file}' not found!")
        return
    
    output_folder = input("Enter output folder (or press Enter for 'downloads'): ").strip()
    if not output_folder:
        output_folder = "downloads"
    
    process_json_file(json_file, output_folder)

if __name__ == "__main__":
    main()
