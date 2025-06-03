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

# Create Flask app
app = Flask(__name__)
CORS(app)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
REQUEST_DELAY = float(os.getenv('REQUEST_DELAY', '1.0'))
MAX_REQUESTS_PER_MINUTE = int(os.getenv('MAX_REQUESTS_PER_MINUTE', '30'))

# Simple rate limiting
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
        logger.info(f"Extracting video ID from: {youtube_url}")
        
        if "watch?v=" in youtube_url:
            video_id = youtube_url.split("watch?v=")[-1].split("&")[0]
        elif "youtu.be/" in youtube_url:
            video_id = youtube_url.split("youtu.be/")[-1].split("?")[0]
        elif "embed/" in youtube_url:
            video_id = youtube_url.split("embed/")[-1].split("?")[0]
        else:
            # Regex fallback
            pattern = r'(?:youtube\.com\/(?:[^\/]+\/.+\/|(?:v|e(?:mbed)?)\/|.*[?&]v=)|youtu\.be\/)([^"&?\/\s]{11})'
            match = re.search(pattern, youtube_url)
            video_id = match.group(1) if match else None
        
        if video_id and len(video_id) == 11:
            logger.info(f"Successfully extracted video ID: {video_id}")
            return video_id
        else:
            logger.error(f"Invalid video ID extracted: {video_id}")
            return None
            
    except Exception as e:
        logger.error(f"Error extracting video ID from {youtube_url}: {e}")
        return None

def fetch_transcript(video_id: str) -> tuple[str, bool, str]:
    """Fetch transcript for YouTube video"""
    try:
        logger.info(f"Fetching transcript for video ID: {video_id}")
        time.sleep(REQUEST_DELAY)
        
        transcript_text = ""
        
        # Method 1: Try default transcript
        try:
            transcript_list = YouTubeTranscriptApi.get_transcript(video_id)
            transcript_text = " ".join([item['text'] for item in transcript_list])
            logger.info(f"Success with default method: {len(transcript_text)} chars")
            
        except Exception as e1:
            logger.info(f"Default method failed: {str(e1)[:100]}")
            
            # Method 2: Try with specific languages
            try:
                transcript_list = YouTubeTranscriptApi.get_transcript(
                    video_id, languages=['en', 'en-US', 'en-GB', 'auto']
                )
                transcript_text = " ".join([item['text'] for item in transcript_list])
                logger.info(f"Success with language method: {len(transcript_text)} chars")
                
            except Exception as e2:
                logger.info(f"Language method failed: {str(e2)[:100]}")
                
                # Method 3: Try any available transcript
                try:
                    transcript_list_obj = YouTubeTranscriptApi.list_transcripts(video_id)
                    available_transcripts = list(transcript_list_obj)
                    
                    if available_transcripts:
                        logger.info(f"Found {len(available_transcripts)} available transcripts")
                        
                        for transcript in available_transcripts:
                            try:
                                transcript_data = transcript.fetch()
                                transcript_text = " ".join([item['text'] for item in transcript_data])
                                logger.info(f"Success with transcript in {transcript.language}: {len(transcript_text)} chars")
                                break
                            except Exception as e_inner:
                                logger.info(f"Failed transcript {transcript.language}: {str(e_inner)[:50]}")
                                continue
                    
                    if not transcript_text:
                        return "", False, "No accessible transcripts found"
                        
                except Exception as e3:
                    logger.warning(f"All transcript methods failed: {str(e3)[:100]}")
                    return "", False, f"No transcript available: {str(e1)[:100]}"
        
        if transcript_text:
            # Clean transcript
            transcript_text = re.sub(r'\[.*?\]', '', transcript_text)  # Remove [Music], etc.
            transcript_text = re.sub(r'\s+', ' ', transcript_text).strip()
            logger.info(f"Transcript cleaned and ready: {len(transcript_text)} chars")
            return transcript_text, True, ""
        else:
            return "", False, "No transcript text extracted"
            
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Transcript fetch failed for {video_id}: {error_msg}")
        return "", False, error_msg

def get_video_metadata(video_id: str) -> Dict[str, str]:
    """Get video metadata using web scraping"""
    try:
        logger.info(f"Fetching metadata for video ID: {video_id}")
        time.sleep(REQUEST_DELAY)
        
        url = f"https://www.youtube.com/watch?v={video_id}"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
        }
        
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Extract title
        title = "Unknown Title"
        title_tag = soup.find('meta', property='og:title')
        if title_tag:
            title = title_tag.get('content', 'Unknown Title')
        elif soup.find('title'):
            title = soup.find('title').get_text().replace(' - YouTube', '').strip()
        
        # Extract description
        description = ""
        desc_tag = soup.find('meta', property='og:description')
        if desc_tag:
            description = desc_tag.get('content', '')
        
        # Extract duration
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
        
        logger.info(f"Metadata extracted - Title: {title[:50]}..., Duration: {duration}")
        
        return {
            'title': title,
            'description': description,
            'duration': duration,
            'url': url
        }
        
    except Exception as e:
        logger.warning(f"Could not fetch metadata for {video_id}: {e}")
        return {
            'title': 'Unknown Title',
            'description': '',
            'duration': 'Unknown',
            'url': f"https://www.youtube.com/watch?v={video_id}"
        }

# Routes
@app.route('/', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'service': 'YouTube Transcript Fetcher API',
        'version': '1.0.0',
        'timestamp': datetime.utcnow().isoformat(),
        'message': 'API is running successfully on Render!'
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
                'error': 'Missing YouTube URL in request body. Send: {"url": "youtube_url_here"}'
            }), 400
        
        youtube_url = data['url']
        include_metadata = data.get('include_metadata', True)
        
        logger.info(f"Processing request for: {youtube_url}")
        
        # Extract video ID
        video_id = extract_video_id(youtube_url)
        if not video_id:
            return jsonify({
                'success': False,
                'error': 'Invalid YouTube URL. Please provide a valid YouTube video URL.'
            }), 400
        
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
        
        # Add metadata if requested
        if include_metadata:
            try:
                metadata = get_video_metadata(video_id)
                response_data.update(metadata)
            except Exception as e:
                logger.warning(f"Metadata fetch failed: {e}")
                response_data.update({
                    'title': 'Unknown Title',
                    'description': '',
                    'duration': 'Unknown'
                })
        
        if not transcript_success:
            response_data['error'] = transcript_error
            logger.warning(f"Transcript fetch failed for {video_id}: {transcript_error}")
            return jsonify(response_data), 404
        
        logger.info(f"Successfully processed {video_id}: {len(transcript)} chars")
        return jsonify(response_data), 200
        
    except Exception as e:
        logger.error(f"API error: {e}")
        return jsonify({
            'success': False,
            'error': f'Internal server error: {str(e)}'
        }), 500

@app.route('/api/status', methods=['GET'])
def get_status():
    """Get API status"""
    return jsonify({
        'status': 'operational',
        'current_load': len(request_timestamps),
        'max_requests_per_minute': MAX_REQUESTS_PER_MINUTE,
        'request_delay': REQUEST_DELAY,
        'timestamp': datetime.utcnow().isoformat(),
        'environment': os.getenv('FLASK_ENV', 'production')
    })

@app.route('/test', methods=['GET'])
def test_endpoint():
    """Simple test endpoint"""
    test_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    video_id = extract_video_id(test_url)
    
    return jsonify({
        'message': 'Test endpoint working',
        'test_url': test_url,
        'extracted_id': video_id,
        'status': 'ok'
    })

# Error handlers
@app.errorhandler(404)
def not_found(error):
    return jsonify({
        'success': False,
        'error': 'Endpoint not found',
        'available_endpoints': [
            'GET /',
            'POST /api/transcript',
            'GET /api/status',
            'GET /test'
        ]
    }), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({
        'success': False,
        'error': 'Internal server error'
    }), 500

# For local development
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV') == 'development'
    app.run(host='0.0.0.0', port=port, debug=debug)

