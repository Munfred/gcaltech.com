# gcaltech.com
Daily emails with events from the Caltech master calendar with links to add them to Google calendar

The Python script `gcaltech.py` fetches the Caltech master calendar HTML (http://www.caltech.edu/master-calendar/day), parses the event info, then discards everything that is not the event tables. Then it creates an even in a public Google calendar for each event from the master calendar, and then replaces "add to ical" links from the original html with "add to google calendar" and corresponding links. Then it does some more html wrangling to make it look pretty.

Then it uses the mailchimp API to send the emails every time it's run. The script currently runs on a Google cloud free tier instance with a daily cron job.

Some references I found useful:

https://ironboundsoftware.com/blog/2018/02/25/daily-email-list-python-mailchimp/
https://developers.google.com/calendar/

Huge thanks to [Will Graf](https://github.com/willgraf) for fixing the code after Caltech updated it's website and broke everything in February 2019. He taught me how to use `.env` files to not commit my secrets.
