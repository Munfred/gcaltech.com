"""Run gCALtech as a Google Cloud Function"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from oauth2client import file
from oauth2client import tools

import argparse
import datetime
import logging
import os
import sys
import tempfile

from googleapiclient import discovery
from lxml import html
import decouple
import httplib2
import icalendar
import oauth2client
import mailchimp3
import requests


APPLICATION_NAME = 'gCALtech'
BASE_URL = 'http://www.caltech.edu'
MASTER_CALENDAR_URL = 'https://www.caltech.edu/campus-life-events/master-calendar/'

# Google Calendar credentials
CLIENT_SECRET_FILE = decouple.config('CLIENT_SECRET_FILE', default='client_secret.json')
CALENDAR_ID = decouple.config('CALENDAR_ID')

SCOPES = ['https://www.googleapis.com/auth/calendar']
FLAGS = None  # override with argparse later

# MailChimp credentials
MAILCHIMP_USERNAME = decouple.config('MAILCHIMP_USERNAME')
MAILCHIMP_API_KEY = decouple.config('MAILCHIMP_API_KEY')
EMAIL_LIST_NAME = decouple.config('EMAIL_LIST_NAME')
REPLY_EMAIL = decouple.config('REPLY_EMAIL')

# Log settings
#LOG_LEVEL = decouple.config('LOG_LEVEL', default='DEBUG')
LOG_LEVEL='CRITICAL'
LOGGER = logging.getLogger(APPLICATION_NAME)


def initialize_logger(log_level='DEBUG'):
    """Start the logger with the given log level"""
    log_level = str(log_level).upper()
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)

    formatter = logging.Formatter('[%(levelname)s]:[%(name)s]: %(message)s')
    console = logging.StreamHandler(stream=sys.stdout)
    console.setFormatter(formatter)

    if log_level == 'DEBUG':
        console.setLevel(logging.DEBUG)
    elif log_level == 'INFO':
        console.setLevel(logging.INFO)
    elif log_level == 'WARN':
        console.setLevel(logging.WARN)
    elif log_level == 'WARNING':
        console.setLevel(logging.WARNING)
    elif log_level == 'ERROR':
        console.setLevel(logging.ERROR)
    elif log_level == 'CRITICAL':
        console.setLevel(logging.CRITICAL)
    else:
        raise ValueError('Log Level "%s" is unrecognized.' % log_level)

    logger.addHandler(console)


def get_credentials(credential_dir):
    logger = logging.getLogger('{}.auth'.format(APPLICATION_NAME))

    if not os.path.exists(credential_dir):
        os.makedirs(credential_dir)

    credential_path = os.path.join(credential_dir, 'gcaltech_certificate.json')
    store = oauth2client.file.Storage(credential_path)

    credentials = store.get()

    if not credentials or credentials.invalid:
        flow = oauth2client.client.flow_from_clientsecrets(
            CLIENT_SECRET_FILE, SCOPES)

        flow.user_agent = APPLICATION_NAME

        credentials = oauth2client.tools.run_flow(flow, store, FLAGS)

        logger.info('Storing credentials to %s.', credential_path)

    return credentials


def relative_href_to_absolute(htmlstring):
    """Converts relative HREF links in HTML to absolute using the base_url"""
    return htmlstring.replace('href="/', 'href="%s/' % BASE_URL)


def parse_event_time(event_time):
    """Parse event time, or return default time"""
    strtime = str(event_time)
    strtime = strtime if strtime[11:] else strtime[:10] + '17:00:00-08:00'
    return strtime[:10] + 'T' + strtime[11:]


def get_calendar_event(cal):
    """Find the VEVENT in the calendar and parse the data as JSON"""
    for component in cal.walk():
        if component.name == 'VEVENT':
            summary = component.get('summary', ' ')
            description = component.get('description', ' ')
            location = component.get('location', ' ')
            startdt = component.get('dtstart').dt
            enddt = component.get('dtend').dt
            enddt = startdt if enddt.day > startdt.day else enddt
            return {
                'summary': '{} - {}'.format(summary, location),
                'location': '{} === {}'.format(location, description),
                'description': description,
                'start': {
                    'dateTime': parse_event_time(startdt),
                    'timeZone': 'America/Los_Angeles',
                },
                'end': {
                    'dateTime': parse_event_time(enddt),
                    'timeZone': 'America/Los_Angeles',
                },
            }
    # there should always be a VEVENT in the icalendar event
    raise ValueError('No VEVENT component found in icalendar event.')


def get_email_html():
    """Scrape the calendar and return links as HTML content for the email"""
    logger = logging.getLogger('{}.calendar'.format(APPLICATION_NAME))

    # Create gcal API to build calendar links
    with tempfile.TemporaryDirectory() as tempdir:
        tempdir = './'
        credentials = get_credentials(tempdir)  # pass tempdir for security
        http = credentials.authorize(httplib2.Http())
        service = discovery.build('calendar', 'v3', http=http)

    response = requests.get(MASTER_CALENDAR_URL)
    tree = html.fromstring(response.text)

    # Get the main HTML to recycle for the email contents
    source_html = tree.xpath('//div[@class="event-listing-block__events"]')
    source_html = html.tostring(source_html[0]).decode('utf8')

    # Get the main calendar CSS styles
    styles = tree.xpath('//head/link[@rel="stylesheet"]')
    styles = [html.tostring(x).decode('utf8') for x in styles]

    # Update relative URLs
    styles = [relative_href_to_absolute(x) for x in styles]
    source_html = relative_href_to_absolute(source_html)

    # Get the event links
    results = tree.xpath('//div[contains(@class, "time-box")]/a/@href')

    # Generate links and set them in the HTML
    for ics_url in results:
        cal_link = str(ics_url).lower()
        response = requests.get(cal_link.replace('webcal://', 'http://'))

        gcal = icalendar.Calendar.from_ical(response.text)

        event = get_calendar_event(gcal)  # parse the event as JSON data

        # pylint: disable=no-member,line-too-long
        event = service.events().insert(calendarId=CALENDAR_ID, body=event).execute()
        # pylint: enable=no-member,line-too-long

        event_link = event.get('htmlLink')
        logger.info('Created new gcal event: %s', event_link)

        source_html = source_html.replace(ics_url, event_link)



    logger.info('Wrote HTML successfully.')

    return source_html


class Email(object):  # pylint: disable=useless-object-inheritance
    """Create and send the email with MailChimp"""

    def __init__(self, list_name, mc_api, mc_user):
        self.list_name = list_name

        self.logger = logging.getLogger('{}.email'.format(APPLICATION_NAME))
        self.client = self.get_mailchimp_client(api=mc_api, user=mc_user)

        self.list_id = self.get_list_id(list_name)
        self.campaign = self.create_campaign()

    @classmethod
    def get_mailchimp_client(cls, api, user):
        """Returns a mailchimp3.MailChimp client"""
        return mailchimp3.MailChimp(mc_api=api, mc_user=user)

    def get_list_id(self, list_name):
        """Get the id for a given list name"""
        list_id = None
        fields = 'lists.name,lists.id'
        response = self.client.lists.all(get_all=True, fields=fields)
        lists = response.get('lists', [])
        for lst in lists:
            if lst.get('name', '') == list_name:
                list_id = lst.get('id')
                break
        else:
            raise ValueError('Failed to find list with name `%s`' % list_name)
        self.logger.debug('Found ID for list `%s`: %s', list_name, list_id)
        return list_id

    def create_campaign(self):
        """Create a MailChimp email campaign"""
        today = datetime.date.today()
        subject_line = '{} {}' \
                       ' 💌📅🎉'.format(APPLICATION_NAME, today)

        campaign_data = {
            'settings': {
                'title': '{} {}'.format(APPLICATION_NAME, today),
                'subject_line': subject_line,
                'from_name': APPLICATION_NAME,
                'reply_to': REPLY_EMAIL,
            },
            'recipients': {
                'list_id': self.list_id
            },
            'type': 'regular'
        }

        campaign = self.client.campaigns.create(campaign_data)
        self.logger.info('Created new campaign with ID %s', campaign['id'])
        return campaign

    def set_html_contents(self, html_contents):
        """Update the email campaign with HTML content"""
        plain_text = 'For plain_text content, please view the master ' \
                     ' calendar: {}'.format(MASTER_CALENDAR_URL)
        return self.client.campaigns.content.update(
            campaign_id=self.campaign['id'],
            data={'html': html_contents, 'plain_text': plain_text})

    def send(self):
        """Send the campaign email to the recipients in self.list_name"""
        result = self.client.campaigns.actions.send(self.campaign['id'])
        self.logger.info('Successfully sent new email campaign %s to list %s.',
                         self.campaign['id'], self.list_name)
        return result

print('💩')
if __name__ == '__main__':
    # pylint: disable=invalid-name
    initialize_logger(LOG_LEVEL)
    print('💩  💩')

    #FLAGS = argparse.ArgumentParser(parents=[oauth2client.tools.argparser]).parse_args()

    html_content = get_email_html()
    print('💩  💩  💩')
    ### Some serious HTML wrangling below, brace yourselves


    # replace dashes on the times with a linebreak
    html_content = html_content.replace('<span class="time-box__times__line">&#8211;</span>','<br>')

    # remove stupid 'learn more >'
    html_content = html_content.replace('Learn More &gt;','')

    # sometimes theres an annoying bullet point for academic calendar, remove the point
    html_content=html_content.replace('<li class="event-listing-block__event__categories__oval mb-4">', '')


    # reformat date into a table column
    html_content=html_content.replace('<div class="time-box__times p-3 d-md-flex flex-md-column justify-content-md-around p-md-4">',
     """
     <td class="event-date" style="background-color:#FFFFFF;border:1px solid #EB7035;border-radius:3px;color:#000000;text-align:center;text-decoration:none;font-size:11;width:6em;">
     <div class="time-box__times p-3 d-md-flex flex-md-column justify-content-md-around p-md-4">
     """)

    # dont touch the multiline below, it needs to be exactly like this to catch the </a>
    html_content=html_content.replace(
    """<i class="icon icon-fa-calendar"></i> Add to Cal
  </a>""", 
  '<br> 📅 add </a> </td>')


    # add new table row in between events
    html_content=html_content.replace('<div class="time-box d-flex justify-content-between flex-md-column justify-content-md-start">', 
    """
    </td>
    </tr>
    <tr> 
    <div class="time-box d-flex justify-content-between flex-md-column justify-content-md-start">
    """)

    # put event into in a table column with a white border to give some extra space
    html_content=html_content.replace('<div class="event-listing-block__event__info">', 
    """
    <td class="event-date" style="background-color:#FFFFFF;border:0em solid rgb(255, 255, 255);border-radius:3px;color:#000000;text-align:left;text-decoration:none;font-size:12;width:100%;">
    <div class="event-listing-block__event__info">
    """)


    print('💩  💩  💩  💩')

    # add a header and table footer
    html_header = """
    <span style="font-family: Arial, Helvetica, sans-serif; font-size: 12px; color: #000000;">
    <table class="events day">
    <tbody>
    """

    html_footer = """
    </td>
    </tr>
    </tbody>
    </table>
    <br><a href="http://gcaltech.netlify.com/">gCALtech</a> is made with 💖 by the 
    <a href="https://gsc.caltech.edu/communications/">GSC</a> and 
    <a href="https://github.com/willgraf"> Will Graf </a>
    """

    
    html_content = html_header + html_content + html_footer

    email = Email(
        list_name=EMAIL_LIST_NAME,
        mc_api=MAILCHIMP_API_KEY,
        mc_user=MAILCHIMP_USERNAME)
    print('💩  💩  💩  💩  💩')

    email.set_html_contents(html_content)
    
    
    # for debugging the html it can be written somewhere
    html_file= open("/mnt/c/Users/beltr/Desktop/email_html.html","w")
    html_file.write(html_content)
    html_file.close()
    email.send()
    print('email sent!!! 💩 💩 💩 💩 💩')
