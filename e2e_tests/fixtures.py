"""
Authoritative Test Fixtures, Mock Data, and Real Hindi Wikipedia Article Extracts.
Derived directly from live probes, ORIGINAL_REQUEST.md, and survey reports.
"""
from typing import Any, Dict


# Citoid API mock responses
MOCK_CITOID_TIMESOFINDIA = [
    {
        "itemType": "webpage",
        "title": "टाइम्स ऑफ इंडिया: ताज़ा ख़बरें और विश्लेषण",
        "url": "https://timesofindia.indiatimes.com/news",
        "websiteTitle": "The Times of India",
        "date": "2026-11-20",
        "accessDate": "2026-11-21",
        "language": "hi",
    }
]

MOCK_CITOID_NATURE_DOI = [
    {
        "itemType": "journalArticle",
        "title": "Nanometre-scale thermometry in a living cell",
        "publicationTitle": "Nature",
        "volume": "500",
        "issue": "7460",
        "date": "2013-08",
        "DOI": "10.1038/nature12373",
        "PMID": "23925244",
        "pages": "54-58",
        "author": [["G.", "Kucsko"], ["P. C.", "Maurer"]],
        "accessDate": "2026-11-21",
    }
]

MOCK_CITOID_JAGRAN_NEWS = [
    {
        "itemType": "newspaperArticle",
        "title": "दैनिक जागरण: राष्ट्रीय समाचार",
        "url": "https://www.jagran.com/news/national-12345.html",
        "publicationTitle": "दैनिक जागरण",
        "date": "2026-11-19",
        "accessDate": "2026-11-21",
        "language": "hi",
    }
]

# Wayback Machine API mock responses
MOCK_WAYBACK_AVAILABLE = {
    "archived_snapshots": {
        "closest": {
            "status": "200",
            "available": True,
            "url": "https://web.archive.org/web/20261120143825/https://example.com/dead-link",
            "timestamp": "20261120143825",
        }
    }
}

MOCK_WAYBACK_EMPTY: Dict[str, Any] = {
    "archived_snapshots": {}
}


# MediaWiki Action API mock responses
MOCK_MEDIAWIKI_ADMIN_USER = {
    "query": {
        "users": [
            {
                "userid": 651050,
                "name": "विक्रम प्रताप",
                "groups": ["*", "user", "autoconfirmed", "sysop", "interface-admin"],
                "rights": ["protect", "delete", "editprotected"],
            }
        ]
    }
}

MOCK_MEDIAWIKI_REGULAR_USER = {
    "query": {
        "users": [
            {
                "userid": 999999,
                "name": "StandardEditor",
                "groups": ["*", "user", "autoconfirmed"],
                "rights": ["read", "edit"],
            }
        ]
    }
}

MOCK_MEDIAWIKI_WIKIPEDIA_SYSOP = {
    "query": {
        "users": [
            {
                "userid": 123456,
                "name": "RandomWikiSysop",
                "groups": ["*", "user", "autoconfirmed", "sysop"],
                "rights": ["protect", "delete"],
            }
        ]
    }
}

# On-wiki dynamic admin config subpage
MOCK_ONWIKI_ADMINS_JSON = {
    "admins": [
        "CoAdminUser1",
        "सहायक_प्रशासक"
    ],
    "kill_switch": False,
    "rate_limit_per_minute": 30,
    "last_updated": "2026-11-21T08:00:00Z"
}

# Real Hindi Wikipedia Article Extracts
CHANDRAYAAN_3_EXTRACT = """
'''चंद्रयान-3''' भारतीय अंतरिक्ष अनुसंधान संगठन (इसरो) द्वारा भेजा गया तीसरा भारतीय चंद्र मिशन है।<ref name="आजतक23">https://aajtak.in/science/story/chandrayaan-3-moon-landing-isro-history-1763175-2023-08-23</ref>
इसमें एक लैंडर और एक रोवर शामिल था।<ref name="isro_press">https://www.isro.gov.in/Chandrayaan3_Details.html</ref>
23 अगस्त 2023 को लैंडर ने चंद्रमा के दक्षिणी ध्रुव पर सफल लैंडिंग की।<ref name="आजतक23" />
मिशन के प्रमुख वैज्ञानिक एस. सोमनाथ थे।<ref>[https://timesofindia.indiatimes.com/india/chandrayaan-3-isro-chief-s-somanath/articleshow/102985392.cms सोमनाथ का वक्तव्य]</ref>

== संदर्भ ==
{{Reflist|30em}}
"""  # noqa: E501

MAHATMA_GANDHI_EXTRACT = """
'''मोहनदास करमचंद गांधी''' (2 अक्टूबर 1869 - 30 जनवरी 1948) भारत एवं भारतीय स्वतंत्रता आंदोलन के एक प्रमुख राजनीतिक एवं आध्यात्मिक नेता थे।<ref>{{Cite book |title=सत्य के प्रयोग |author=गांधी |publisher=नवजीवन ट्रस्ट |year=1927}}</ref>
उन्हें महात्मा के नाम से भी जाना जाता है।<ref>https://www.gandhiheritageportal.org/biography</ref>
अहिंसा के मार्ग पर चलते हुए उन्होंने सत्याग्रह का नेतृत्व किया।<ref>{{Cite web |url=https://www.mkgandhi.org/articles/satyagraha.htm |title=गांधीजी का सत्याग्रह |deadurl=yes |accessdate=2020-01-15}}</ref>
गांधीजी ने हरिजन उत्थान के लिए अथक प्रयास किए।<ref>https://www.gandhiheritageportal.org/biography</ref>

== संदर्भ सूची ==
{{सन्दर्भ सूची}}
"""  # noqa: E501

BHARAT_EXTRACT = """
'''भारत''' दक्षिण एशिया में स्थित भारतीय उपमहाद्वीप का सबसे बड़ा देश है।<ref>https://www.india.gov.in/overview</ref>
इसकी राजधानी नई दिल्ली है।<ref>https://twitter.com/in_gov_update/status/123456789</ref>
भारत विश्व का सबसे अधिक आबादी वाला लोकतांत्रिक राष्ट्र है।<ref>{{Cite web |title={{lang-hi|भारत गणराज्य}} |url=https://censusindia.gov.in/data</ref>

== संदर्भ ==
{{reflist}}
"""  # noqa: E501

HINDI_WIKIPEDIA_EXTRACT = """
'''हिन्दी विकिपीडिया''' विकिपीडिया का हिन्दी भाषा का संस्करण है।<ref>[https://hi.wikipedia.org/wiki/विशेष:आंकड़े हिन्दी विकिपीडिया आंकड़े]</ref>
इसकी शुरुआत जुलाई 2003 में हुई थी।<ref>{{Cite web |url=https://meta.wikimedia.org/wiki/Hindi_Wikipedia |title=मेटा-विकी हिन्दी |deadurl=no |archiveurl=http://web.archive.org/web/20150101/meta.wikimedia.org |archivedate=20150101}}</ref>
इसमें 1.5 लाख से अधिक लेख मौजूद हैं।<ref name="stats">https://stats.wikimedia.org/hi</ref>
ज्ञान के प्रसार में इसका महत्वपूर्ण योगदान है।<ref name="stats" />
"""  # noqa: E501

DELHI_EXTRACT = """
'''दिल्ली''' भारत की राजधानी और एक केंद्र शासित प्रदेश है।<ref group="टिप्पणी">दिल्ली राष्ट्रीय राजधानी क्षेत्र (एनसीआर) का केंद्र है।</ref>
यहाँ संसद भवन और राष्ट्रपति भवन स्थित हैं।<ref>https://delhi.gov.in/history</ref>
यमुना नदी इसके किनारे बहती है।<ref>https://delhi.gov.in/history</ref>

== टिप्पणी ==
{{Reflist|group=टिप्पणी}}

== संदर्भ ==
{{Reflist}}
"""  # noqa: E501
