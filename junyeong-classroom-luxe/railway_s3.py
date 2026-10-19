"""Private Railway object storage. Credentials remain in service variables."""
import os
import re
from functools import lru_cache
from urllib.parse import urlsplit


def configured():
    return all(os.getenv('VIDEO_S3_'+key, '').strip()
               for key in ('ENDPOINT', 'BUCKET', 'ACCESS_KEY_ID', 'SECRET_ACCESS_KEY'))


def cfg():
    if not configured():
        raise RuntimeError('영상 저장소 연결 설정이 없습니다.')
    endpoint = os.environ['VIDEO_S3_ENDPOINT'].strip().rstrip('/')
    bucket = os.environ['VIDEO_S3_BUCKET'].strip()
    parsed = urlsplit(endpoint)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username:
        raise ValueError('Invalid storage endpoint')
    if not re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]', bucket):
        raise ValueError('Invalid bucket')
    return dict(endpoint=endpoint, bucket=bucket,
                key_id=os.environ['VIDEO_S3_ACCESS_KEY_ID'].strip(),
                secret=os.environ['VIDEO_S3_SECRET_ACCESS_KEY'].strip())


@lru_cache(maxsize=1)
def client():
    import boto3
    from botocore.config import Config
    c = cfg()
    return boto3.client('s3', endpoint_url=c['endpoint'],
        region_name=os.getenv('VIDEO_S3_REGION', 'auto'),
        aws_access_key_id=c['key_id'], aws_secret_access_key=c['secret'],
        config=Config(signature_version='s3v4',
            s3={'addressing_style':os.getenv('VIDEO_S3_ADDRESSING_STYLE', 'virtual')},
            connect_timeout=15, read_timeout=90,
            retries={'mode':'standard','max_attempts':3},
            request_checksum_calculation='when_required',
            response_checksum_validation='when_required')), c['bucket']


def origin():
    if not configured():
        return ''
    c = cfg()
    host = urlsplit(c['endpoint']).netloc
    return 'https://' + (c['bucket']+'.' if os.getenv('VIDEO_S3_ADDRESSING_STYLE','virtual')=='virtual' else '') + host


def signed_url(key, method='GET', seconds=300):
    s3, bucket = client()
    return s3.generate_presigned_url('head_object' if method=='HEAD' else 'get_object',
        Params={'Bucket':bucket,'Key':key}, ExpiresIn=seconds, HttpMethod=method)


def redirect(key, method='GET'):
    from fastapi.responses import RedirectResponse
    return RedirectResponse(signed_url(key, method),status_code=307,
        headers={'Cache-Control':'private, no-store','Referrer-Policy':'no-referrer'})


def configure_cors(site_origin):
    s3, bucket = client()
    s3.put_bucket_cors(Bucket=bucket,CORSConfiguration={'CORSRules':[{
        'AllowedOrigins':[site_origin], 'AllowedMethods':['GET','HEAD','PUT'],
        'AllowedHeaders':['*'], 'ExposeHeaders':['ETag','Content-Range','Content-Length','Accept-Ranges'],
        'MaxAgeSeconds':3600}]})
