import feedparser
import logging
import re

from BeautifulSoup import BeautifulSoup, SoupStrainer

from google.appengine.api import xmpp
from google.appengine.ext import db
from google.appengine.ext import webapp
from google.appengine.api import urlfetch
from google.appengine.api import memcache
from google.appengine.ext.webapp import util
from google.appengine.api.labs import taskqueue

class User(db.Model):
    userid = db.StringProperty()
    trackers = db.StringListProperty()

class MainHandler(webapp.RequestHandler):
    def get(self):
        self.response.out.write('Coming soon... ')
        

class SendCommentaryHandler(webapp.RequestHandler):
    def get(self):
        matches = getMatches()
        
        for url, title in matches.items():
            taskqueue.add(url='/sendmatchcommentary', params={'url': url, 'title': title})


class SendMatchCommentaryHandler(webapp.RequestHandler):
    def post(self):
        commentary_url = self.request.get('url')
        match = self.request.get('title')
        comments = getNewComments(commentary_url)

        users = getUsers()
        for user in users:
            if xmpp.get_presence(user.userid):
                for track in user.trackers:
                    if match.upper().find(track.upper()) > -1 and comments != '':
                        xmpp.send_message(user.userid, comments)
        
        
class SendMatchScoreHandler(webapp.RequestHandler):
    def get(self):
        
        ''' fetch cricket scores '''
        score_url = 'http://www.cricinfo.com/rss/livescores.xml'
        result = urlfetch.fetch(score_url)
        if result.status_code == 200:
            cricket_scores = []
            channels = feedparser.parse(result.content)            
            for entry in channels.entries:
                cricket_scores.append(entry.description)
            
            users = getUsers()

            for user in users:
                msg = ''
                if xmpp.get_presence(user.userid):
                    for track in user.trackers:
                        for score in cricket_scores:
                            if score.upper().find(track.upper()) > -1 and score != memcache.get(user.userid + track):
                                memcache.set(key= user.userid + track, value= score, time = 86400)
                                msg = msg + score + "\n"
                    
                    if msg != '':
                        xmpp.send_message(user.userid, msg)
                            
        
class XMPPHandler(webapp.RequestHandler):
    
    def post(self):
        message = xmpp.Message(self.request.POST)
        sender = getUserEmailId(message.sender)
        if message.body[0:5].lower() == 'track':
            user = getUser(sender)
            track = message.body[5: len(message.body)].strip()
            
            if track == '' :
                message.reply("You are currently tracking :" + ", ".join(user.trackers))
                return
            
            if user.trackers.count(track) > 0:
                message.reply("Already tracking :" + track)
                return
            else:
                user.trackers.append(track)
                user.put()
                message.reply("Now tracking :" + track)
                
        elif message.body[0:7].lower() == 'untrack':
            user = getUser(sender)
            untrack = message.body[7: len(message.body)].strip()
            
            if user.trackers.count(untrack) == 0:
                message.reply("You are not tracking: " + untrack + " anyways.")
            else:
                user.trackers.remove(untrack)
                user.put()
                message.reply("Stopped tracking: " + untrack)
            
        elif message.body[0:4].lower() == 'help':
            message.reply("\n type track <space> keyword to track. E.g. track India will track matches that India plays. \n\n type 'ongoing' to find out current matches \n\n  type untrack <keyword> to stop following a match \n\n type just 'track' to see the matches you are following.")
            return
        
        elif message.body[0:7].lower() == 'ongoing':
            matches = getMatches()
            titles = []
            
            for url, title in matches.items():
                titles.append(title)
            
            msg = "\n".join(titles)
            
            if(msg == ''):
                message.reply("No ongoing matches")
            else:
                message.reply(msg)
            return
        
        
def getMatches():
    commentary_main_url = 'http://cricket.plusmo.com/cricket/wap'
    
    result = urlfetch.fetch(commentary_main_url)

    commentary_urls = []
    commentary_titles =  []
    
    matches_dictionary  = {}
    
    if result.status_code == 200:
        doc = result.content
        links = SoupStrainer('a', href=re.compile('commentary'))
        tags = [tag for tag in BeautifulSoup(doc, parseOnlyThese=links)]
        
        for tag in tags:
            matches_dictionary[tag.get('href')] = ", ".join(tag.contents)
    
    return matches_dictionary

def getLatestCommentary():
    commentary_main_url = 'http://cricket.plusmo.com/cricket/wap'
    result = urlfetch.fetch(commentary_main_url)

    commentary_urls = []
    commentary_titles =  []
    
    comments_dictionary  = {}
    
    if result.status_code == 200:
        doc = result.content
        links = SoupStrainer('a', href=re.compile('commentary'))
        tags = [tag for tag in BeautifulSoup(doc, parseOnlyThese=links)]
        
        for tag in tags:
            commentary_urls.append(tag.get('href'))
            commentary_titles.append(", ".join(tag.contents))
        
    count = 0
    for commentary_url in commentary_urls:
        comments = getNewComments(commentary_url)
        match_title = commentary_titles[count]
        logging.info('match title and comments' + match_title + ", " + comments)
        comments_dictionary[match_title] = comments
        count = count + 1
    
    return comments_dictionary

def getNewComments(commentary_url):
    new_comments = []
    old_comments = memcache.get(commentary_url)

    if old_comments is None:
        old_comments = []
    
    #logging.info('old_comments in cache: %s' % str(old_comments))
    result = urlfetch.fetch('http://cricket.plusmo.com' + commentary_url)
    if result.status_code == 200:
        doc = result.content
        soup = BeautifulSoup(doc)
        tags =  soup.findAll("div", { "class" : "detail" })

        for tag in tags:
            comment = ''
            for content in tag.contents:
                comment =  comment + str(content)

            comment = comment.replace('<br />', '')
            comment = comment.replace('<strong>', '*')
            comment = comment.replace('</strong>', '*')

            '''if nothing interesting happened, continue with next comment'''
            if (comment.find('OUT') == -1 and comment.find('FOUR') == -1 and comment.find('SIX') == -1 and comment.find('WIN') == -1 and comment.find('4 runs') == -1):
                continue;
            
            #see if this comment is new. else, nothing much to do.
            if old_comments.count(comment) == 0:
                new_comments.append(comment)
                old_comments.insert(0, comment)

                if len(old_comments) > 20:
                    old_comments.pop()
        
        memcache.set(commentary_url, old_comments, 86400)
        #logging.info('new old_comments in cache: %s' % str(old_comments))
        new_comments.reverse()
        
    return "\n".join(new_comments)

def getUserEmailId(string):
    return string.partition('/')[0]

        
def getUser(userid):
    users = db.GqlQuery("SELECT * FROM User WHERE userid = :1", userid)
    user = users.get()
    
    if user is None:
        """ Add a user with this id """
        user = User(userid = userid)
        user.put()
        
    return user

def getUsers():
    users = []
    query = User.all()

    for user in query:
        users.append(user)

    return users;
            
def main():
    application = webapp.WSGIApplication([('/_ah/xmpp/message/chat/', XMPPHandler),
                                          ('/sendscore', SendMatchScoreHandler),
                                          ('/sendcommentary', SendCommentaryHandler),
                                          ('/sendmatchcommentary', SendMatchCommentaryHandler),
                                        ('/', MainHandler)],
                                       debug=True)
    util.run_wsgi_app(application)


if __name__ == '__main__':
  main()
