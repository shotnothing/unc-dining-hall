import logging
import os
from fuzzywuzzy import fuzz
from telegram import ForceReply, Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

logging.basicConfig(
    filename='log.txt',
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", 
    level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)





from bs4 import BeautifulSoup
import requests
from pprint import pprint
import pandas as pd
pd.options.mode.chained_assignment = None
import datetime as dt
from tqdm import tqdm

def get_menu(date_range, locations={'chase': 'Chase', 'top-of-lenoir': 'Lenoir'}):
    out = []
    
    for date in tqdm(date_range):
        for location, location_display_name in locations.items():
            url = f'https://dining.unc.edu/locations/{location}/?date={date.strftime("%Y-%m-%d")}'
            response = requests.get(url)
            html_content = response.content

            soup = BeautifulSoup(html_content, 'html.parser')

            periods = [
                    period.text
                    for period 
                    in soup.find_all('div', class_='c-tabs-nav__link-inner')
            ]

            for period_idx, tab in enumerate(soup.find_all('div', class_='c-tab')):
                period = periods[period_idx]

                for station in tab.find_all('div', class_='menu-station'):
                    station_name = station.find('h4', class_='toggle-menu-station-data').text

                    for item in station.find_all('li', class_='menu-item-li'):
                        item_name = item.find('a', class_='show-nutrition').text

                        period_start = period[period.rfind("(") + 1 : period.rfind("-")]
                        period_end = period[period.rfind("-") + 1 : period.rfind(")")]

                        if ':' not in period_start:
                            period_start = f'{period_start[:-2]}:00{period_start[-2:]}'

                        if ':' not in period_end:
                            period_end = f'{period_end[:-2]}:00{period_end[-2:]}'

                        out.append({
                            'date': date,
                            'location': location_display_name,
                            'period': period,
                            'station': station_name,
                            'item': item_name,
                            'period_start': period_start,
                            'period_end': period_end,
                        })

    return pd.DataFrame(out)


class Menu:
    cache = {}

    intresting_stations = [
        'The Griddle',
        'The Kitchen Table',
        'Rotisserie',
        'International Flavors ',
        'Homemade Soups & Sushi',
        'International Flavors',
        'Soup and Salads',
        'Simply Prepared Grill',
        'Specialty Bakery',
    ]

    interesting_food = [ # (exclude, reinclude_list)
        ('Sauce', ['in', 'with']),
        ('Dip', ['in', 'with']),
    ]

    def __init__(self, df = None):
        if df is None:
            self.df = self.fetch()
        else:
            self.df = df

        df['date'] = pd.to_datetime(df['date'], format="%Y-%m-%d").dt.date # does pandas implicity convert date but not time?
        df['period_start'] = pd.to_datetime(df['period_start'], format="%H:%M:%S").dt.time
        df['period_end'] = pd.to_datetime(df['period_end'], format="%H:%M:%S").dt.time

    @staticmethod
    def fetch():
        df = get_menu(pd.date_range(start='2023-08-16', end='2023-12-20')) #TODO: make this dynamic window
        df = df.join(
            df.drop(columns=['station']).drop_duplicates()['item'].value_counts(),
            on='item',
            how='left',
            rsuffix='_count'
        )
        df['item_prob'] = df['item_count'] / len(df[['date', 'location', 'period']].drop_duplicates())
        df['date'] = pd.to_datetime(df['date'], format="%Y-%m-%d").dt.date
        df['period_start'] = pd.to_datetime(df['period_start'], format="%I:%M%p").dt.time
        df['period_end'] = pd.to_datetime(df['period_end'], format="%I:%M%p").dt.time
        
        df.to_csv('unc_dining_with_counts.csv')
        return Menu(df)

    def filter_date(self, date = dt.datetime.today()):
        if date == 'tommorow':
            date = (dt.datetime.today() + dt.timedelta(days=1)).date()
        elif date == 'today':
            date = dt.datetime.today().date()
        elif type(date) == dt.datetime:
            date = date.date()
            
        return Menu(self.df[self.df['date'] == date])

    def filter_location(self, location):
        return Menu(self.df[self.df['location'] == location])
    
    def filter_time(self, time = dt.datetime.now().time()):
        if time == 'lunch':
            time = dt.time(13)
        if time == 'dinner':
            time = dt.time(18)

        return Menu(self.df[
            (self.df['period_start'] <= time) & (
                (self.df['period_end'] >= time) | (self.df['period_end'] == dt.time(0))
            )])

    def filter_common(self, threshold = 0.2):
        return Menu(self.df[self.df['item_prob'] < threshold])
    
    def filter_generic(self, column, f):
        return Menu(self.df[f(self.df[column])])
    
    def sort_values(self, *args, **kwargs):
        return Menu(self.df.sort_values(*args, **kwargs))

    def use_cache(**expiry):
        def partial(method):
            def wrapper(self, *args, **kwargs):
                key = (method.__name__, args, tuple(kwargs.items()))

                if key in Menu.cache \
                    and Menu.cache[key]['expiry'] > dt.datetime.now():
                    print(f'Using cache for {key}, TTL: {Menu.cache[key]["expiry"] - dt.datetime.now()}')
                    return Menu.cache[key]['data']
                
                out = method(self, *args, **kwargs)
                Menu.cache[key] = {
                    'data': out,
                    'expiry': dt.datetime.now() + dt.timedelta(**expiry)
                }
                return out
            return wrapper
        return partial

    @use_cache(minutes=30)
    def get_daily_overview(self, date=dt.datetime.today()):
        highlights = {}

        if date == 'tommorow':
            date = (dt.datetime.today() + dt.timedelta(days=1)).date()
        elif date == 'today':
            date = dt.datetime.today().date()
        elif type(date) == dt.datetime:
            date = date.date()
        
        for location in self.df['location'].unique():
            for time in ['lunch', 'dinner']:
                filtered = menu \
                    .filter_common() \
                    .filter_generic('station', lambda x: x.isin(Menu.intresting_stations)) \
                    .filter_generic('item', lambda x: x.str.contains('Sauce|Dip', case=False, regex=True) == False) \
                    .sort_values(by=['item_prob'], ascending=True) \
                    .filter_date(date) \
                    .filter_time(time) \
                    .filter_location(location)[:8]
                highlight = filtered['item'] + filtered['item_prob'].apply(lambda x: f' ({x*100:.0f}%)')
                highlight = '    ' + '\n    '.join(highlight)
                
                if len(filtered) == 0:
                    highlight = '    No highlights!'
                    period = 'N/A'
                else:
                    period = filtered['period'].iloc[0]

                highlights[(location, time)] = highlight, period

        out_lunch = f'''
\U0001f3d9 <u><b>Highlights for {date.strftime("%A, %d %b")} Lunch</b></u>:
Format: Item (Rarity)

\U0001f3df <b>Chase {highlights[('Chase', 'lunch')][1]}</b>:
{highlights[('Chase', 'lunch')][0]}

\U0001f3db <b>Lenoir {highlights[('Lenoir', 'lunch')][1]}</b>:
{highlights[('Lenoir', 'lunch')][0]}
'''
        out_dinner = f'''
\U0001f306 <u><b>Highlights for {date.strftime("%A, %d %b")} Dinner</b></u>:
Format: Item (Rarity)

\U0001f3df <b>Chase {highlights[('Chase', 'dinner')][1]}</b>:
{highlights[('Chase', 'dinner')][0]}

\U0001f3db <b>Lenoir {highlights[('Lenoir', 'dinner')][1]}</b>:
{highlights[('Lenoir', 'dinner')][0]}
'''   
        return out_lunch, out_dinner

    def filter_item(self, item, exact=False):
        if exact:
            return item, Menu(self.df[self.df['item'] == item])
        else:
            search_list = list(self.df['item'].unique()) #TODO: cache this
            search_list.sort(key=lambda x: fuzz.ratio(x, item), reverse=True)
            return search_list[0], Menu(self.df[self.df['item'] == search_list[0]])

    @use_cache(days=1)
    def get_item(self, item):
        newline = '\n'
        result = self.filter_item(item)
        past = result[1][result[1]['date']<=dt.datetime.today().date()][::-1][:5]
        future = result[1][result[1]['date']>dt.datetime.today().date()][:5]
        past_str = past.apply(lambda x: f"{x['location']}, {x['date'].strftime('%A, %d %b')}", axis=1)
        future_str = future.apply(lambda x: f"{x['location']}, {x['date'].strftime('%A, %d %b')}", axis=1)

        out = f'''Best Match: <b>{result[0]}</b>
<b><u>Past</u></b>:
{newline.join(past_str)}

<b><u>Future</u></b>:
{newline.join(future_str)}
        '''
        return out

    def __repr__(self):
        return self.df.__repr__()
    
    def _repr_html_(self):
        return self.df._repr_html_ ()
    
    def __getitem__(self, i):
         return self.df[i]






menu = Menu(pd.read_csv('unc_dining_with_counts.csv'))




from telegram import KeyboardButton, ReplyKeyboardMarkup

subscriber_keyboard_markup = ReplyKeyboardMarkup([
    [KeyboardButton("Today's Highlights"),
     KeyboardButton("Tommorow's Highlights")],
    [KeyboardButton("Item Search"),
     KeyboardButton("Unsubscribe to Daily Highlights")],
])

subscriber_keyboard_markup = ReplyKeyboardMarkup([
    [KeyboardButton("Today's Highlights"),
     KeyboardButton("Tommorow's Highlights")],
    [KeyboardButton("Item Search"),
     KeyboardButton("Subscribe to Daily Highlights")],
])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await update.message.reply_html(
        rf"Hi {user.mention_html()}!",
        reply_markup=ForceReply(selective=True),
    )

async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    out = menu.get_daily_overview('today')
    await update.message.reply_text(out[0], parse_mode='HTML')
    await update.message.reply_text(out[1], parse_mode='HTML', reply_markup=subscriber_keyboard_markup)

async def tommorow_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    out = menu.get_daily_overview('tommorow')
    await update.message.reply_text(out[0], parse_mode='HTML')
    await update.message.reply_text(out[1], parse_mode='HTML', reply_markup=subscriber_keyboard_markup)

async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.message.text[6:].strip()
    if len(query)  == 0:
        await update.message.reply_text('Enter search in this format: "search <item>\n\nExample: "search chicken florentine"')
        return
    
    out = menu.get_item(query)
    await update.message.reply_text(out, parse_mode='HTML', reply_markup=subscriber_keyboard_markup)

async def default_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message.text == "Today's Highlights":
        await today_command(update, context)

    elif update.message.text == "Tommorow's Highlights":
        await tommorow_command(update, context)

    elif update.message.text == "Item Search":
        await update.message.reply_text('Enter search in this format: "search <item>\n\nExample: "search chicken florentine"')

    elif update.message.text[:6] == "search":
        await search_command(update, context)

    elif update.message.text == "Subscribe to Daily Highlights":
        await update.message.reply_text('WIP')
    
    else:
        await update.message.reply_text(
            "Pick an option:",
            reply_markup=subscriber_keyboard_markup,
            )
    


def main() -> None:
    token = os.getenv('TELEGRAM_DINING_HALL_BOT_TOKEN')
    if token is None:
        raise ValueError("No Telegram bot token found in environment variables")

    application = Application.builder().token(token).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, default_callback))

    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()