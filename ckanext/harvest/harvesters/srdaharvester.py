#coding: utf-8
import urllib2

from ckan.lib.base import c
from ckan import model
from ckan import plugins as p
from ckan.model import Session, Package
from ckan.logic import ValidationError, NotFound, get_action
from ckan.lib.helpers import json

from ckanext.harvest.model import HarvestJob, HarvestObject, HarvestGatherError, \
                                    HarvestObjectError
from hashlib import sha1
import logging
log = logging.getLogger(__name__)

from base import HarvesterBase

from lxml import html
import re
from datetime import datetime
from pylons import config

class SRDAHarvester(HarvesterBase):
    '''
    A Harvester for SRDA database
    '''
    config = None
    
    api_version = 2

    PREFIX_URL = "https://srda.sinica.edu.tw"
    CATALOGUE_INDEX_URL = "/search/field/2"

    def _set_config(self,config_str):
        if config_str:
            self.config = json.loads(config_str)
            self.api_version = int(self.config['api_version'])
            log.debug('Using config: %r', self.config)
        else:
            self.config = {}

    def info(self):
        return {
            'name': 'opendata_srda',
            'title': 'SRDA',
            'description': 'Survey Research Data Archive',
            'form_config_interface':'Text'
        }

    def gather_stage(self,harvest_job):
        log.debug('In SRDAHarvester gather_stage (%s)' % harvest_job.source.url)

        get_all_packages = True
        package_ids = []

	data = urllib2.urlopen(self.PREFIX_URL + self.CATALOGUE_INDEX_URL)
        doc = html.parse(data)
        for td in doc.findall("//td[@class='left_p12_title']/a"):
            link = td.get('href')
            if re.match(r"/search/fsciitem", link):
                id = sha1(link).hexdigest()
                obj = HarvestObject(guid=id, job= harvest_job, content=link)
                obj.save()
                package_ids.append(obj.id)
	
        self._set_config(harvest_job.source.config)

        # Check if this source has been harvested before
        previous_job = Session.query(HarvestJob) \
                        .filter(HarvestJob.source==harvest_job.source) \
                        .filter(HarvestJob.gather_finished!=None) \
                        .filter(HarvestJob.id!=harvest_job.id) \
                        .order_by(HarvestJob.gather_finished.desc()) \
                        .limit(1).first()

        return package_ids

    def fetch_stage(self,harvest_object):
        log.debug('In SRDAHarvester fetch_stage')

        self._set_config(harvest_object.job.source.config)

        # Get contents
        try:
	    data = urllib2.urlopen(self.PREFIX_URL + harvest_object.content)
	    doc = html.parse(data)
	    package_dict = {'extras': {}, 'resources': [], 'tags': []}

	    meta = dict()

	    table = doc.find(".//table[@cellpadding='5'][@width='94%']")
	    for tr in table.findall("tr"):
		td = tr.find("td[@bgcolor='#C6C4A4']")
		if td.text is None:
		    continue
		key = td.text.strip()
		td = tr.find("td[@bgcolor='#FFFFFF']")
		value = td.text.strip()
		meta[key] = value
	    package_dict["title"] = meta[u"計畫名稱"]
	    package_dict["author"] = meta[u"計畫主持人"]
	    package_dict["notes"] = meta[u"摘要"]
	    #package_dict["metadata_modified"] = datetime.today().strftime("%Y-%m-%d")
	    package_dict["extras"][u"資料集網址"] = self.PREFIX_URL + harvest_object.content
	    package_dict["extras"][u"登錄號"] = meta[u"登錄號"]
	    package_dict["extras"][u"學門類型"] = meta[u"學門類型"]
	    package_dict["extras"][u"叢集名稱"] = meta[u"叢集名稱"]
	    package_dict["extras"][u"計畫執行單位"] = meta[u"計畫執行單位"]
	    package_dict["extras"][u"計畫委託單位"] = meta[u"計畫委託單位"]
	    package_dict["extras"][u"計畫執行期間"] = meta[u"計畫執行期間"]
	    package_dict["extras"][u"調查執行期間"] = meta[u"調查執行期間"]

	    if "關鍵字".decode("utf8") in meta.keys():
		package_dict["tags"] = meta[u"關鍵字"].split(u"、")

	    res_field = table.find(".//table[@cellpadding='5']")
	    for tr in res_field.findall(".//tr"):
		a = tr.find("td[@bgcolor='#FFFFFF']//a")
		# if file is not private
		if a is not None:
		    res = self.PREFIX_URL + a.get("href")
		    des = tr.find("td[@bgcolor='#C6C4A4']").text.strip()
		    package_dict["resources"].append({
			"url": res,
			"format": a.text[-3:],
			"description": des
		    })

	    package_dict["license_id"] = "odc-odbl"
	    harvest_object.content = json.dumps(package_dict,ensure_ascii=False)

	except Exception,e:
            self._save_object_error('Unable to get content for package: %s: %r' % ("", e),harvest_object)
            return False

        # Save the fetched contents in the HarvestObject
        harvest_object.save()
        return True

    def import_stage(self,harvest_object):
        log.debug('In SRDAHarvester import_stage')
        if not harvest_object:
            log.error('No harvest object received')
            return False

        if harvest_object.content is None:
            self._save_object_error('Empty content for object %s' % harvest_object.id,
                    harvest_object, 'Import')
            return False
 
        #self._set_config(harvest_object.job.source.config)
        try:
            package_dict = json.loads(harvest_object.content)
	    package_dict["id"] = harvest_object.guid
	    package_dict["extras"][u"資料庫名稱"] = u'SRDA'
	    package_dict["extras"][u"資料庫網址"] = u'http://srda.sinica.edu.tw/'

	    #print package_dict
            
	    for key in package_dict['extras'].keys():
                if not isinstance(package_dict['extras'][key], basestring):
                    try:
                        package_dict['extras'][key] = json.dumps(package_dict['extras'][key])
                    except TypeError:
                        # If converting to a string fails, just delete it.
                        del package_dict['extras'][key]

            result = self._create_or_update_package(package_dict,harvest_object)

            return True
        except ValidationError,e:
            self._save_object_error('Invalid package with GUID %s: %r' % (harvest_object.guid, e.error_dict),
                    harvest_object, 'Import')
        except Exception, e:
            self._save_object_error('%r'%e,harvest_object,'Import')

