from __future__ import division

import re
import json
import requests
from urlparse import urljoin
from html5lib import HTMLParser
from html5lib.treebuilders import getTreeBuilder
from lxml.cssselect import CSSSelector


XHTML_NAMESPACE = 'http://www.w3.org/1999/xhtml'
html_parser = HTMLParser(
    tree=getTreeBuilder('lxml')
)


def compile_selector(selector):
    return CSSSelector(selector, namespaces={'html': XHTML_NAMESPACE})


find_project_cards = compile_selector('html|div.project-card')
find_title = compile_selector('html|h1#title html|a')
find_card_title = compile_selector('html|h2 html|strong html|a')
find_money_raised = compile_selector('#moneyraised')
find_backers_count = compile_selector('#backers_count')
find_pledged = compile_selector('#pledged')
find_rewards = compile_selector('.NS-projects-reward')
find_h3 = compile_selector('html|h3')
find_reward_backers = compile_selector('.backers-limits html|span.num-backers')
find_limit_box = compile_selector('.backers-limits html|span.limited .limited-number')


_pledge_re = re.compile('Pledge.*?(\d+(?:[.,]\d+)*) or more')
_left_re = re.compile('of (\d+) left\)')
_num_re = re.compile('\d+')


def parse_html(string_or_stream):
    return html_parser.parse(string_or_stream, encoding='utf-8',
                             useChardet=False)


def extract_data(element):
    rv = {}
    for key, value in element.attrib.iteritems():
        if key.startswith('data-'):
            rv[key[5:]] = value
    for child in element.iterchildren():
        rv.update(extract_data(child))
    return rv


class ProjectScraper(object):
    BASE_URL = 'http://www.kickstarter.com/'

    def __init__(self, project_url):
        self.project_url = project_url
        self._overview_page = None

    def get_overview_page(self):
        if self._overview_page is not None:
            return self._overview_page
        with requests.session() as sess:
            rv = sess.get(urljoin(self.BASE_URL, self.project_url))
            self._overview_page = rv = parse_html(rv.content)
            return rv

    def get_summary(self):
        html = self.get_overview_page()
        box = find_money_raised(html)[0]
        backers = extract_data(find_backers_count(box)[0])
        pledged = extract_data(find_pledged(box)[0])

        return {
            'backers':      int(backers['value']),
            'goal':         float(pledged['goal']),
            'pledged':      float(pledged['pledged']),
            'currency':     pledged['currency']
        }

    def get_backer_breakdown(self):
        result = []
        for reward in find_rewards(self.get_overview_page()):
            bracket = _pledge_re.search(find_h3(reward)[0].text).group(1)
            backers = find_reward_backers(reward)[0]
            try:
                limit_box = find_limit_box(reward)[0]
                limit = int(_left_re.search(limit_box.text).group(1))
            except IndexError:
                limit = None

            result.append({
                'bracket':      float(bracket.replace(',', '')),
                'backers':  int(_num_re.search(backers.text).group(0)),
                'limit':    limit
            })
        return result

    def get_title(self):
        return find_title(self.get_overview_page())[0].text.strip()

    def get_all(self):
        return {
            'title':        self.get_title(),
            'summary':      self.get_summary(),
            'breakdown':    self.get_backer_breakdown()
        }


class CategoryScraper(object):
    CATEGORY_URL = 'http://www.kickstarter.com/discover/categories/'
    project_scraper_class = ProjectScraper

    def __init__(self, category_slug):
        self.category_slug = category_slug
        self._overview_page = None

    def get_overview_page(self):
        if self._overview_page is not None:
            return self._overview_page
        with requests.session() as sess:
            rv = sess.get('%s/%s/most-funded' % (
                self.CATEGORY_URL,
                self.category_slug
            ))
            self._overview_page = rv = parse_html(rv.content)
            return rv

    def describe_project(self, card):
        title = find_card_title(card)[0]
        return {
            'title':        title.text,
            'url':          title.attrib['href'].split('?', 1)[0]
        }

    def describe_all_projects(self):
        rv = []
        for card in find_project_cards(self.get_overview_page()):
            rv.append(self.describe_project(card))
        return rv

    def iter_scrape_all_projects(self):
        for project in self.describe_all_projects():
            yield self.project_scraper_class(project['url']).get_all()


class FunnyMath(object):

    def __init__(self, projects, currency='USD'):
        self.projects = projects
        self.currency = currency

    def iter_projects(self, limit_currency=True):
        for project in self.projects:
            if project['summary']['currency'] == self.currency:
                yield project

    def list_averages(self):
        rv = []
        for project in self.iter_projects():
            avg = project['summary']['pledged'] / project['summary']['backers']
            rv.append((project['title'], avg))
        rv.sort(key=lambda x: -x[1])
        return rv

    def list_contributions_greater(self, threshold):
        rv = []
        for project in self.iter_projects():
            totals = 0.0
            max = 0.0
            for reward in project['breakdown']:
                if reward['bracket'] < threshold:
                    continue
                totals += reward['backers'] * reward['bracket']
                if reward['limit'] is None:
                    max = float('inf')
                else:
                    max += reward['limit'] * reward['bracket']
            rv.append((project['title'], totals, max, totals / max))
        rv.sort(key=lambda x: -x[-1])
        return rv

    def list_fund_status(self):
        rv = []
        for project in self.iter_projects(limit_currency=False):
            rv.append((project['title'], project['summary']['pledged'],
                       project['summary']['goal'],
                       project['summary']['pledged'] / project['summary']['goal']))
        rv.sort(key=lambda x: -x[-1])
        return rv

    def _list_reward_levels_for_project(self, project):
        rewards = sorted(project['breakdown'], key=lambda x: x['bracket'])
        project_total = project['summary']['pledged']
        total = 0.0
        rv = []
        for idx, reward in enumerate(rewards):
            try:
                upper = rewards[idx + 1]['bracket']
            except IndexError:
                upper = float('inf')
            this_bracket = reward['bracket'] * reward['backers']
            pct = this_bracket / project_total
            rv.append((reward['bracket'], upper, this_bracket, pct))
            total += this_bracket
        this_bracket = project['summary']['pledged'] - total
        rv.insert(0, (0.0, rv[0][0], this_bracket, this_bracket / project_total))
        return rv

    def list_reward_levels(self):
        rv = []
        for project in self.iter_projects(limit_currency=False):
            rv.append((project['title'], project['summary']['pledged'],
                       self._list_reward_levels_for_project(project)))
        return rv


def load_projects():
    with open('projects.json') as f:
        return json.load(f)


def scrape_and_save():
    scraper = CategoryScraper('video games')
    projects = list(scraper.iter_scrape_all_projects())
    with open('projects.json', 'w') as f:
        json.dump(projects, f, indent=2)


def print_average_contributions():
    projects = load_projects()
    fm = FunnyMath(projects)
    print 'Average contributions by project in USD:'
    for project, avg in fm.list_averages():
        print '  %-60s%13.2f' % (project, avg)


def print_approximate_contributions_above(threshold=5000.0):
    projects = load_projects()
    fm = FunnyMath(projects)
    print 'Contributions above %.2f USD:' % threshold
    for project, actual, max, pct in fm.list_contributions_greater(threshold):
        print '  %-60s%13.2f out of %.2f (%.0f %%)' % (project, actual, max, pct * 100)


def print_fund_status():
    projects = load_projects()
    fm = FunnyMath(projects)
    print 'Funded in percent:'
    for project, pledged, goal, pct in fm.list_fund_status():
        print '  %-60s%13.2f out of %.2f (%.0f %%)' % (project, pledged, goal, pct * 100)


def print_contributions_per_reward_level():
    projects = load_projects()
    fm = FunnyMath(projects)
    print 'Contributions per reward level:'
    for project, total, reward_levels in fm.list_reward_levels():
        print '  %s (%.2f)' % (project, total)
        for bracket_min, bracket_max, bracket_total, pct in reward_levels:
            print '    %10.2f - %-10.2f  %10.2f  %8.2f %%' % \
                (bracket_min, bracket_max, bracket_total, pct * 100)


def all_together():
    print_average_contributions()
    print
    print_approximate_contributions_above()
    print
    print_fund_status()
    print
    print_contributions_per_reward_level()


#main = scrape_and_save
#main = print_average_contributions
#main = print_approximate_contributions_above
#main = contributions_per_reward_level
main = all_together


if __name__ == '__main__':
    main()
