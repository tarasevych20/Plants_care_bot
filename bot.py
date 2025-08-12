{\rtf1\ansi\ansicpg1251\cocoartf2822
\cocoatextscaling0\cocoaplatform0{\fonttbl\f0\fswiss\fcharset0 Helvetica;}
{\colortbl;\red255\green255\blue255;}
{\*\expandedcolortbl;;}
\paperw11900\paperh16840\margl1440\margr1440\vieww11520\viewh8400\viewkind0
\pard\tx720\tx1440\tx2160\tx2880\tx3600\tx4320\tx5040\tx5760\tx6480\tx7200\tx7920\tx8640\pardirnatural\partightenfactor0

\f0\fs24 \cf0 import telebot\
import requests\
from datetime import datetime\
from config import TOKEN, CITY, OPENWEATHER_API_KEY\
\
bot = telebot.TeleBot(TOKEN)\
\
# \uc0\u1043 \u1088 \u1072 \u1092 \u1110 \u1082  \u1076 \u1086 \u1075 \u1083 \u1103 \u1076 \u1091  \u1079 \u1072  \u1088 \u1086 \u1089 \u1083 \u1080 \u1085 \u1072 \u1084 \u1080  (\u1074  \u1076 \u1085 \u1103 \u1093 )\
plants_schedule = \{\
    "\uc0\u1044 \u1088 \u1072 \u1094 \u1077 \u1085 \u1072 ": \{"poliv": 14, "pidzh": None\},\
    "\uc0\u1050 \u1072 \u1083 \u1072 \u1084 \u1086 \u1085 \u1076 \u1080 \u1085 ": \{"poliv": 3, "pidzh": 14\},\
    "\uc0\u1061 \u1072 \u1084 \u1072 \u1077 \u1076 \u1086 \u1088 \u1077 \u1103 ": \{"poliv": 5, "pidzh": 30\},\
    "\uc0\u1047 \u1072 \u1084 \u1110 \u1086 \u1082 \u1091 \u1083 \u1100 \u1082 \u1072 \u1089 ": \{"poliv": 14, "pidzh": 42\},\
    "\uc0\u1057 \u1087 \u1072 \u1090 \u1110 \u1092 \u1110 \u1083 \u1091 \u1084 ": \{"poliv": 4, "pidzh": 14\}\
\}\
\
# \uc0\u1047 \u1073 \u1077 \u1088 \u1110 \u1075 \u1072 \u1108 \u1084 \u1086  \u1086 \u1089 \u1090 \u1072 \u1085 \u1085 \u1110  \u1076 \u1072 \u1090 \u1080  (\u1084 \u1086 \u1078 \u1085 \u1072  \u1073 \u1091 \u1076 \u1077  \u1079 \u1072 \u1084 \u1110 \u1085 \u1080 \u1090 \u1080  \u1085 \u1072  \u1041 \u1044 )\
last_done = \{plant: \{"poliv": None, "pidzh": None\} for plant in plants_schedule\}\
\
def get_weather():\
    url = f"http://api.openweathermap.org/data/2.5/weather?q=\{CITY\}&appid=\{OPENWEATHER_API_KEY\}&units=metric&lang=ua"\
    r = requests.get(url).json()\
    temp = r["main"]["temp"]\
    weather = r["weather"][0]["description"]\
    return temp, weather\
\
def generate_plan():\
    temp, weather = get_weather()\
    today = datetime.now().date()\
    tasks = []\
\
    for plant, schedule in plants_schedule.items():\
        # \uc0\u1055 \u1077 \u1088 \u1077 \u1074 \u1110 \u1088 \u1082 \u1072  \u1087 \u1086 \u1083 \u1080 \u1074 \u1091 \
        if schedule["poliv"]:\
            last = last_done[plant]["poliv"]\
            if not last or (today - last).days >= schedule["poliv"]:\
                tasks.append(f"\uc0\u1055 \u1086 \u1083 \u1080 \u1081  \{plant\}")\
\
        # \uc0\u1055 \u1077 \u1088 \u1077 \u1074 \u1110 \u1088 \u1082 \u1072  \u1087 \u1110 \u1076 \u1078 \u1080 \u1074 \u1083 \u1077 \u1085 \u1085 \u1103 \
        if schedule["pidzh"]:\
            last = last_done[plant]["pidzh"]\
            if not last or (today - last).days >= schedule["pidzh"]:\
                tasks.append(f"\uc0\u1055 \u1110 \u1076 \u1078 \u1080 \u1074 \u1080  \{plant\}")\
\
    plan = f"\uc0\u1044 \u1086 \u1073 \u1088 \u1086 \u1075 \u1086  \u1088 \u1072 \u1085 \u1082 \u1091 ! \u55356 \u57137 \\n\u1055 \u1086 \u1075 \u1086 \u1076 \u1072 : \{temp\}\'b0C, \{weather\}\\n\u1057 \u1100 \u1086 \u1075 \u1086 \u1076 \u1085 \u1110 :\\n- " + "\\n- ".join(tasks) if tasks else "\u1057 \u1100 \u1086 \u1075 \u1086 \u1076 \u1085 \u1110  \u1076 \u1086 \u1075 \u1083 \u1103 \u1076  \u1085 \u1077  \u1087 \u1086 \u1090 \u1088 \u1110 \u1073 \u1077 \u1085 ."\
    return plan\
\
@bot.message_handler(commands=["start"])\
def start(message):\
    bot.send_message(message.chat.id, "\uc0\u1055 \u1088 \u1080 \u1074 \u1110 \u1090 ! \u1071  \u1090 \u1074 \u1110 \u1081  \u1073 \u1086 \u1090  \u1076 \u1083 \u1103  \u1076 \u1086 \u1075 \u1083 \u1103 \u1076 \u1091  \u1079 \u1072  \u1088 \u1086 \u1089 \u1083 \u1080 \u1085 \u1072 \u1084 \u1080  \u55356 \u57151 . \u1065 \u1086 \u1073  \u1086 \u1090 \u1088 \u1080 \u1084 \u1072 \u1090 \u1080  \u1087 \u1083 \u1072 \u1085  \u1085 \u1072  \u1089 \u1100 \u1086 \u1075 \u1086 \u1076 \u1085 \u1110 , \u1085 \u1072 \u1087 \u1080 \u1096 \u1080  /plan")\
\
@bot.message_handler(commands=["plan"])\
def send_plan(message):\
    plan = generate_plan()\
    bot.send_message(message.chat.id, plan)\
\
bot.polling(none_stop=True)\
}