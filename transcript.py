# app.py - YouTube Fetching API Backend for Render
from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import time
import logging
import re
from datetime import datetime
from typing import Optional, Dict, Any
import requests
from bs4 import BeautifulSoup

# YouTube transcript API
from youtube_transcript_api import YouTubeTranscriptApi

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Rate limiting configuration
REQUEST_DELAY = float(os.getenv('REQUEST_DELAY', '1.0'))
MAX_REQUESTS_PER_MINUTE = int(os.getenv('MAX_REQUESTS_PER_MINUTE', '30'))

# Simple in-memory rate limiting
request_timestamps = []

def rate_limit_check():
    """Simple rate limiting check"""
    now = time.time()
    global request_timestamps
    
    # Remove timestamps older than 1 minute
    request_timestamps = [ts for ts in request_timestamps if now - ts < 60]
    
    if len(request_timestamps) >= MAX_REQUESTS_PER_MINUTE:
        return False
    
    request_timestamps.append(now)
    return True

def extract_video_id(youtube_url: str) -> Optional[str]:
    """Extract YouTube video ID from URL"""
    try:
        if "watch?v=" in youtube_url:
            return youtube_url.split("watch?v=")[-1].split("&")[0]
        elif "youtu.be/" in youtube_url:
            return youtube_url.split("youtu.be/")[-1].split("?")[0]
        elif "embed/" in youtube_url:
            return youtube_url.split("embed/")[-1].split("?")[0]
        else:
            # Regex fallback
            pattern = r'(?:youtube\.com\/(?:[^\/]+\/.+\/|(?:v|e(?:mbed)?)\/|.*[?&]v=)|youtu\.be\/)([^"&?\/\s]{11})'
            match = re.search(pattern, youtube_url)
            return match.group(1) if match else None
    except Exception as e:
        logger.error(f"Error extracting video ID: {e}")
        return None

def fetch_transcript(video_id: str) -> tuple[str, bool, str]:
    """Fetch transcript for YouTube video"""
    try:
        time.sleep(REQUEST_DELAY)  # Rate limiting
        
        # Try multiple methods to get transcript
        transcript_text = ""
        
        # Method 1: Default transcript
        try:
            transcript_list = YouTubeTranscriptApi.get_transcript(video_id)
            transcript_text = " ".join([item['text'] for item in transcript_list])
            logger.info(f"Got transcript using default method: {len(transcript_text)} chars")
            
        except Exception as e1:
            # Method 2: Try with language codes
            try:
                transcript_list = YouTubeTranscriptApi.get_transcript(
                    video_id, languages=['en', 'en-US', 'en-GB']
                )
                transcript_text = " ".join([item['text'] for item in transcript_list])
                logger.info(f"Got transcript using language method: {len(transcript_text)} chars")
                
            except Exception as e2:
                # Method 3: Try any available transcript
                try:
                    transcript_list_obj = YouTubeTranscriptApi.list_transcripts(video_id)
                    for transcript in transcript_list_obj:
                        try:
                            transcript_data = transcript.fetch()
                            transcript_text = " ".join([item['text'] for item in transcript_data])
                            logger.info(f"Got transcript ({transcript.language}): {len(transcript_text)} chars")
                            break
                        except:
                            continue
                except Exception as e3:
                    return "", False, f"No transcript available: {str(e1)[:100]}"
        
        if transcript_text:
            # Clean transcript
            transcript_text = re.sub(r'\[.*?\]', '', transcript_text)
            transcript_text = re.sub(r'\s+', ' ', transcript_text).strip()
            return transcript_text, True, ""
        else:
            return "", False, "No transcript text extracted"
            
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Transcript fetch failed: {error_msg}")
        return "", False, error_msg

def get_video_metadata(video_id: str) -> Dict[str, str]:
    """Get video metadata using web scraping"""
    try:
        time.sleep(REQUEST_DELAY)
        
        url = f"https://www.youtube.com/watch?v={video_id}"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Extract title
        title = "Unknown Title"
        title_tag = soup.find('meta', property='og:title')
        if title_tag:
            title = title_tag.get('content', 'Unknown Title')
        
        # Extract description
        description = ""
        desc_tag = soup.find('meta', property='og:description')
        if desc_tag:
            description = desc_tag.get('content', '')
        
        # Extract duration from JSON-LD
        duration = "Unknown"
        try:
            json_scripts = soup.find_all('script', type='application/ld+json')
            for script in json_scripts:
                try:
                    import json
                    data = json.loads(script.string)
                    if isinstance(data, list):
                        data = data[0] if data else {}
                    
                    if data.get('@type') == 'VideoObject':
                        duration_iso = data.get('duration', '')
                        if duration_iso and duration_iso.startswith('PT'):
                            # Parse ISO 8601 duration
                            pattern = r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?'
                            match = re.match(pattern, duration_iso)
                            if match:
                                hours = int(match.group(1)) if match.group(1) else 0
                                minutes = int(match.group(2)) if match.group(2) else 0
                                seconds = int(match.group(3)) if match.group(3) else 0
                                
                                if hours > 0:
                                    duration = f"{hours}h {minutes}m {seconds}s"
                                elif minutes > 0:
                                    duration = f"{minutes}m {seconds}s"
                                else:
                                    duration = f"{seconds}s"
                        break
                except:
                    continue
        except:
            pass
        
        return {
            'title': title,
            'description': description,
            'duration': duration,
            'url': url
        }
        
    except Exception as e:
        logger.warning(f"Could not fetch metadata: {e}")
        return {
            'title': 'Unknown Title',
            'description': '',
            'duration': 'Unknown',
            'url': f"https://www.youtube.com/watch?v={video_id}"
        }

# API Routes

@app.route('/', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'service': 'YouTube Transcript Fetcher API',
        'version': '1.0.0',
        'timestamp': datetime.utcnow().isoformat()
    })

@app.route('/api/transcript', methods=['POST'])
def get_transcript():
    """Get transcript for YouTube video"""
    
    # Rate limiting
    if not rate_limit_check():
        return jsonify({
            'success': False,
            'error': 'Rate limit exceeded. Try again later.',
            'rate_limit': {
                'max_requests_per_minute': MAX_REQUESTS_PER_MINUTE,
                'current_requests': len(request_timestamps)
            }
        }), 429
    
    try:
        # Get request data
        data = request.get_json()
        if not data or 'url' not in data:
            return jsonify({
                'success': False,
                'error': 'Missing YouTube URL in request body'
            }), 400
        
        youtube_url = data['url']
        include_metadata = data.get('include_metadata', True)
        
        # Extract video ID
        video_id = extract_video_id(youtube_url)
        if not video_id:
            return jsonify({
                'success': False,
                'error': 'Invalid YouTube URL'
            }), 400
        
        logger.info(f"Processing video: {video_id}")
        
        # Fetch transcript
        transcript, transcript_success, transcript_error = fetch_transcript(video_id)
        
        # Prepare response
        response_data = {
            'success': transcript_success,
            'video_id': video_id,
            'url': youtube_url,
            'transcript': transcript,
            'transcript_length': len(transcript),
            'timestamp': datetime.utcnow().isoformat()
        }
        
        # Add metadata if requested and transcript fetch was successful
        if include_metadata:
            metadata = get_video_metadata(video_id)
            response_data.update(metadata)
        
        if not transcript_success:
            response_data['error'] = transcript_error
            return jsonify(response_data), 404
        
        logger.info(f"Successfully processed video {video_id}: {len(transcript)} chars")
        return jsonify(response_data)
        
    except Exception as e:
        logger.error(f"API error: {e}")
        return jsonify({
            'success': False,
            'error': f'Internal server error: {str(e)}'
        }), 500

@app.route('/api/batch', methods=['POST'])
def get_batch_transcripts():
    """Get transcripts for multiple YouTube videos"""
    
    # Rate limiting (stricter for batch)
    if not rate_limit_check():
        return jsonify({
            'success': False,
            'error': 'Rate limit exceeded. Try again later.'
        }), 429
    
    try:
        data = request.get_json()
        if not data or 'urls' not in data:
            return jsonify({
                'success': False,
                'error': 'Missing URLs array in request body'
            }), 400
        
        urls = data['urls']
        if not isinstance(urls, list) or len(urls) == 0:
            return jsonify({
                'success': False,
                'error': 'URLs must be a non-empty array'
            }), 400
        
        # Limit batch size
        max_batch_size = 5
        if len(urls) > max_batch_size:
            return jsonify({
                'success': False,
                'error': f'Batch size limited to {max_batch_size} URLs'
            }), 400
        
        results = []
        
        for url in urls:
            video_id = extract_video_id(url)
            if not video_id:
                results.append({
                    'url': url,
                    'success': False,
                    'error': 'Invalid YouTube URL'
                })
                continue
            
            transcript, success, error = fetch_transcript(video_id)
            
            result = {
                'url': url,
                'video_id': video_id,
                'success': success,
                'transcript': transcript,
                'transcript_length': len(transcript)
            }
            
            if not success:
                result['error'] = error
            
            results.append(result)
            
            # Add delay between batch requests
            time.sleep(REQUEST_DELAY * 2)
        
        return jsonify({
            'success': True,
            'total_processed': len(results),
            'results': results,
            'timestamp': datetime.utcnow().isoformat()
        })
        
    except Exception as e:
        logger.error(f"Batch API error: {e}")
        return jsonify({
            'success': False,
            'error': f'Internal server error: {str(e)}'
        }), 500

@app.route('/api/status', methods=['GET'])
def get_status():
    """Get API status and usage info"""
    return jsonify({
        'status': 'operational',
        'current_load': len(request_timestamps),
        'max_requests_per_minute': MAX_REQUESTS_PER_MINUTE,
        'request_delay': REQUEST_DELAY,
        'timestamp': datetime.utcnow().isoformat()
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV') == 'development'
    app.run(host='0.0.0.0', port=port, debug=debug)
