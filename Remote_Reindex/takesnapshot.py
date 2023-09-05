import boto3
import requests
from requests_aws4auth import AWS4Auth

host = 'https://search-oosdomainname-35ib24hlxbuz2swpmfvuvpmdge.us-east-1.es.amazonaws.com/' #replace
region = 'us-east-1' #replace
service = 'es'
credentials = boto3.Session().get_credentials()
awsauth = AWS4Auth(credentials.access_key, credentials.secret_key, region, service, session_token=credentials.token)

path = '_snapshot/ossmanualsnapshotpath/snapshotfoldername' #replace
url = host + path

r = requests.put(url, auth=awsauth)

print(r.text)