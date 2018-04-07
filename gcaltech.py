# This Python file uses the following encoding: utf-8
import httplib2
import urllib.request as urllib2
import os
from googleapiclient import discovery
from oauth2client import client
from oauth2client import tools
from oauth2client.file import Storage
import icalendar
import datetime
import re

from mailchimp3 import MailChimp
import os
import sys
import datetime

USERNAME = "gcaltech" ### You should use your username here
SECRET_KEY = "XXXXXXXXXXXX"  ### You should use your mailchimp key here

client = MailChimp(SECRET_KEY, USERNAME)


def get_list_id(list_name):
    """Get the id for a given list name"""
    lists = client.lists.all()
    for l in lists.get("lists", []):
        if l.get("name", "") == list_name:
            return l.get("id")


def create_campaign(campaign_data):
    """ Create a campaign """
    camp = client.campaigns.create(campaign_data)
    # print(camp)
    return camp


def populate_campagin_data(list_id):
    campaign_data = {"settings": {"title": "gCALtech %s " % datetime.date.today(),
                                  "subject_line": "gCALtech: %s Caltech Events for Google Calendar on your Inbox 💌📅🎉" % datetime.date.today(),
                                  "from_name": 'gCALtech',
                                  "reply_to": 'edaveiga@caltech.edu',
                                  },
                     "recipients": {"list_id": list_id},
                     "type": "regular"}
    return campaign_data

def populate_content(campaign_id):
    """
    Put the content of the email into the campaign
    :param campaign: The struct genereated by :create_campaign:
    """
    email_contents = make_email()
    data = {"html": email_contents,
            "plain_text": "Go visit the master calendar if you want plaintext lol \n http://www.caltech.edu/master-calendar/day/"}
    result = client.campaigns.content.update(campaign_id=campaign_id, data=data)
    print("\n\nResult:")
    print(result)
    return result

### This part deals with parsing the master calendar and adding the events to a google calendar,
### Then creating the html email contents with links to the events in the google calendar

# some oauth BS i don't know what it does
try:
    import argparse
    flags = argparse.ArgumentParser(parents=[tools.argparser]).parse_args()
except ImportError:
    flags = None

SCOPES = 'https://www.googleapis.com/auth/calendar' 
CLIENT_SECRET_FILE = 'client_secret.json' ### You should get this client secret from the Google calendar API
APPLICATION_NAME = 'gCALtech'

def get_credentials():
    home_dir = os.path.expanduser('.')


    #credential_dir = os.path.join(home_dir, '.credentials')
    credential_dir = home_dir
    print(credential_dir)
    if not os.path.exists(credential_dir):
        os.makedirs(credential_dir)
    credential_path = os.path.join(credential_dir, 'gcaltech_certificate.json') ### You should also get this certificate from Google calendar API

    store = Storage(credential_path)
    credentials = store.get()
    if not credentials or credentials.invalid:
        flow = client.flow_from_clientsecrets(CLIENT_SECRET_FILE, SCOPES)
        flow.user_agent = APPLICATION_NAME
        if flags:
            credentials = tools.run_flow(flow, store, flags)
        else: # Needed only for compatibility with Python 2.6
            credentials = tools.run(flow, store)
        print('Storing credentials to ' + credential_path)
    return credentials

def make_email():
    event_links =[]

    credentials = get_credentials()
    http = credentials.authorize(httplib2.Http())
    service = discovery.build('calendar', 'v3', http=http)

    #fetches HTML with ics links of today events
    req = urllib2.Request('http://www.caltech.edu/master-calendar/day/')
    response = urllib2.urlopen(req)
    data = response.read().decode('utf-8')
    ## chop off the "ongoing" events that last many days
    chopped = data.split('class="day-header">Ongoing</th>')

    # find the webcal links for all of the day events
    result = re.findall('webcal://(.*)\' ', chopped[0])  # regex magick


    for ics_url in result:
        # parses the ics file from the url and grabs the time/location/summary/description for gcal
        req = urllib2.Request('http://' + ics_url)
        response = urllib2.urlopen(req)
        data = response.read().decode('utf-8')
        gcal = icalendar.Calendar.from_ical(data)

        for component in gcal.walk():
            if component.name == "VEVENT":
                summary = component.get('summary')
                description = component.get('description')
                location = component.get('location')
                startdt = component.get('dtstart').dt
                enddt = component.get('dtend').dt
        print(enddt)
        if enddt.day>startdt.day:
            enddt = startdt
        # puts a 'T' in the middle of the date so that it looks like '2017-11-29T6:00:00-08:00' for google calendar...
        print (type(startdt))
        #if not startdt.time():
        #now = datetime.datetime.utcnow().isoformat() + 'Z'  # 'Z' indicates UTC time
        #    enddt=now
        #    startdt = now

        strend = str(enddt)
        if not strend[11:]: #checks to see that there is time info for the event, otherwise just put something there
            strend = strend[:10] + "T17:00:00-08:00"
        gcalend = strend[:10] + 'T' + strend[11:]

        strstart = str(startdt)
        if not strstart[11:]: #checks to see that there is time info for the event, otherwise just put something there
            strstart = strstart[:10] + "T17:00:00-08:00"
        gcalstart = strstart[:10] + 'T' + strstart[11:]


        # fill up the event that goes in gcal
        if summary == None:
            summary = ' '
        if location == None:
            location = ' '
        if description == None:
            description ==' '
        event = {
          'summary': summary + ' - ' + location,
          'location': location + ' === ' + description,
          'description': description,
          'start': {
            'dateTime': gcalstart,
            'timeZone': 'America/Los_Angeles',
          },
          'end': {
            'dateTime': gcalend,
            'timeZone': 'America/Los_Angeles',
          },
        }

        event = service.events().insert(calendarId='tds63v9ookra6o795v65nl2d60@group.calendar.google.com', body=event).execute()
        print ('Event created: %s' % (event.get('htmlLink')))

        #now do shenannigans to change html to link to gcal events
        event_links.append(str(event.get('htmlLink')))


    # some real html wrangling, brace yourselves:
    butchered = chopped[0].split('<div class="view-content">')

    email_contents = butchered[2].split('<div id="footer">')

    email_contents=email_contents[0]

    #email_contents = email_contents.replace("Add this event to my calendar", "Add to Google calendar")

    counter = 0
    for ics_url in result:
        badurl = "webcal://" + ics_url
        email_contents = email_contents.replace(badurl, event_links[counter])
        counter = counter+1
    #print(email_contents)

    dirtyfix = """
    <span style="font-family: Arial, Helvetica, sans-serif; font-size: 12px; color: #000000;">
    """
    email_contents = dirtyfix + email_contents

    email_contents = email_contents.replace('/content/','http://www.caltech.edu/content/')

    email_contents = email_contents.replace('-&nbsp;',' ')
    email_contents = email_contents.replace('class="event-date"','class="event-date" style="background-color:#FFFFFF;border:1px solid #EB7035;border-radius:3px;color:#000000;text-align:center;text-decoration:none;width:5em;"')
    email_contents = email_contents.replace('div class="event-location"','div class="event-location" style ="font-style: italic;"')
    email_contents = email_contents.replace('<img src="/sites/all/modules/date_ical/images/ical-feed-icon-34x14.png" alt="Add this event to my calendar" />', "Add to Google calendar")
    email_contents = email_contents.replace('class="seminar-title"', 'class="seminar-title" style ="font-weight: bold;"')
    email_contents = email_contents.replace("class='ical-icon'", 'style="background-color:#4885ed;border:1px solid #4885ed;border-radius:3px;color:#ffffff;display:inline-block;text-align:center;text-decoration:none;width:5em;"')

    #now we send off the email!


    print(email_contents)
    return email_contents


if __name__ == "__main__":
    list_name = "gcaltech"

    list_id = get_list_id(list_name)
    campdata = populate_campagin_data(list_id)

    camp = create_campaign(campdata)
    populate_content(camp['id'])

    client.campaigns.actions.send(camp['id'])
    print('Sent!')
