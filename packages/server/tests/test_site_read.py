"""A business's own website read for OpenStreetMap (site_read.py): schema.org
structured data only, turned into OSM's tags. No page is fetched here."""

from __future__ import annotations

import datetime as dt
import json

from agent_media_server import site_read

TODAY = dt.date(2026, 11, 22)
URL = "https://bluefin.example/"


def page(*nodes, body: str = "") -> str:
    blocks = "".join(f'<script type="application/ld+json">{json.dumps(n)}</script>' for n in nodes)
    return f"<html><head>{blocks}</head><body>{body}</body></html>"


RESTAURANT = {
    "@context": "https://schema.org", "@type": "Restaurant", "name": "Blue Fin Fish & Chips Blairgowrie",
    "url": "https://bluefin.example/", "telephone": "+61 3 5988 1234", "email": "mailto:hello@bluefin.example",
    "address": {"@type": "PostalAddress", "streetAddress": "2865 Point Nepean Road", "addressLocality": "Blairgowrie",
                "addressRegion": "VIC", "postalCode": "3942", "addressCountry": "AU"},
    "geo": {"@type": "GeoCoordinates", "latitude": -38.36118, "longitude": 144.77844},
    "openingHoursSpecification": [
        {"@type": "OpeningHoursSpecification", "dayOfWeek": ["https://schema.org/Monday", "Tuesday", "Wednesday", "Thursday"],
         "opens": "11:30:00", "closes": "20:00:00"},
        {"@type": "OpeningHoursSpecification", "dayOfWeek": ["Friday", "Saturday"], "opens": "11:30", "closes": "21:00"},
        {"@type": "OpeningHoursSpecification", "dayOfWeek": "Sunday", "opens": "11:30", "closes": "20:00"},
        {"@type": "OpeningHoursSpecification", "dayOfWeek": "https://schema.org/PublicHolidays", "opens": "00:00", "closes": "00:00"},
        {"@type": "OpeningHoursSpecification", "validFrom": "2026-12-25", "validThrough": "2026-12-25", "opens": "00:00", "closes": "00:00"},
        {"@type": "OpeningHoursSpecification", "validFrom": "2025-12-25", "validThrough": "2025-12-25", "opens": "00:00", "closes": "00:00"},
    ],
    "hasMenu": "/menu", "acceptsReservations": "https://bookings.example/bluefin",
    "servesCuisine": ["Fish and Chips", "Seafood", "Something Unheard Of"], "priceRange": "$$",
    "paymentAccepted": "Cash, Credit Card, EFTPOS, Apple Pay",
    "sameAs": ["https://www.instagram.com/bluefin", "https://www.facebook.com/bluefinblairgowrie", "https://x.com/bf"],
    "amenityFeature": [{"@type": "LocationFeatureSpecification", "name": "Wheelchair accessible", "value": True},
                       {"@type": "LocationFeatureSpecification", "name": "Free Wi-Fi", "value": True},
                       {"@type": "LocationFeatureSpecification", "name": "Parking", "value": True}],
    "aggregateRating": {"@type": "AggregateRating", "ratingValue": "4.6", "reviewCount": "212"},
}


def test_a_restaurant_read_into_osm_tags():
    got = site_read.read(page({"@type": "WebSite", "name": "Blue Fin"}, RESTAURANT), URL, "Blue Fin Blairgowrie", TODAY)
    p = got["proposed"]
    assert p["opening_hours"] == "Mo-Th 11:30-20:00; Fr,Sa 11:30-21:00; Su 11:30-20:00; PH off; 2026 Dec 25 off"
    assert p["tags"] == {
        "phone": "+61 3 5988 1234", "email": "hello@bluefin.example", "website": "https://bluefin.example/",
        "addr:housenumber": "2865", "addr:street": "Point Nepean Road", "addr:suburb": "Blairgowrie",
        "addr:postcode": "3942", "addr:state": "VIC", "website:menu": "https://bluefin.example/menu",
        "reservation": "yes", "reservation:website": "https://bookings.example/bluefin",
        "cuisine": "fish_and_chips;seafood", "payment:cash": "yes", "payment:credit_cards": "yes",
        "payment:eftpos": "yes", "payment:apple_pay": "yes",
        "contact:instagram": "https://www.instagram.com/bluefin",
        "contact:facebook": "https://www.facebook.com/bluefinblairgowrie",
        "wheelchair": "yes", "internet_access": "wlan", "internet_access:fee": "no"}
    assert p["fields"]["Cuisine"] == "Fish and Chips, Seafood, Something Unheard Of"
    assert p["fields"]["Position"] == "-38.361180, 144.778440" and "Parking: yes" in p["fields"]["Features"]
    assert p["fields"]["Price range"] == "$$"
    assert got["site"] == {"phone": "+61 3 5988 1234", "menu_url": "https://bluefin.example/menu",
                           "booking_url": "https://bookings.example/bluefin",
                           "cuisine": "Fish and Chips, Seafood, Something Unheard Of", "price_range": "$$",
                           "payment": "Cash, Credit Card, EFTPOS, Apple Pay",
                           "social": {"instagram": "https://www.instagram.com/bluefin",
                                      "facebook": "https://www.facebook.com/bluefinblairgowrie"}}
    # Ratings are for the card only, never for OSM.
    assert got["site_info"] == {"rating": 4.6, "rating_count": 212}
    assert not any("rating" in k for k in p["tags"])


def test_opening_hours_strings_and_microdata():
    html = """<div itemscope itemtype="https://schema.org/CafeOrCoffeeShop">
      <h1 itemprop="name">Little Cove Cafe</h1>
      <meta itemprop="openingHours" content="Mo-Fr 07:00-15:00">
      <meta itemprop="openingHours" content="Sa,Su 08:00-14:00">
      <span itemprop="telephone">03 5988 0000</span>
      <div itemprop="address" itemscope itemtype="https://schema.org/PostalAddress">
        <span itemprop="streetAddress">12 Main St</span>, <span itemprop="addressLocality">Sorrento</span>
      </div></div>"""
    got = site_read.read(html, URL, "Little Cove", TODAY)
    assert got["proposed"]["opening_hours"] == "Mo-Fr 07:00-15:00; Sa,Su 08:00-14:00"
    assert got["proposed"]["tags"]["phone"] == "03 5988 0000"
    assert got["proposed"]["tags"]["addr:street"] == "Main St" and got["proposed"]["tags"]["addr:city"] == "Sorrento"


def test_open_all_day_and_past_midnight():
    n = {"@type": "Bar", "name": "Night Owl", "openingHoursSpecification": [
        {"dayOfWeek": d, "opens": "00:00", "closes": "23:59"} for d in
        ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")]}
    assert site_read.read(page(n), URL, "Night Owl", TODAY)["proposed"]["opening_hours"] == "24/7"
    n = {"@type": "Bar", "name": "Night Owl", "openingHours": "Fr-Sa 18:00-02:00"}
    assert site_read.read(page(n), URL, "Night Owl", TODAY)["proposed"]["opening_hours"] == "Fr,Sa 18:00-02:00"


def test_nothing_without_structured_data_or_on_a_chains_list():
    assert site_read.read("<p>Open Mon-Fri 9-5, call us!</p>", URL, "Blue Fin", TODAY) == {}
    chain = page({"@type": "Restaurant", "name": "Burger Co Frankston", "openingHours": "Mo-Su 10:00-22:00"},
                 {"@type": "Restaurant", "name": "Burger Co Rosebud", "openingHours": "Mo-Su 11:00-21:00"})
    assert site_read.read(chain, URL, "Blue Fin", TODAY) == {}
    # The one by the place's name is taken.
    assert site_read.read(chain, URL, "Burger Co Rosebud", TODAY)["proposed"]["opening_hours"] == "Mo-Su 11:00-21:00"


def test_unreadable_hours_are_left_out():
    n = {"@type": "Store", "name": "Odd Shop", "openingHours": "by appointment", "telephone": "+61 3 9000 0000"}
    got = site_read.read(page(n), URL, "Odd Shop", TODAY)
    assert "opening_hours" not in got["proposed"] and got["proposed"]["tags"] == {"phone": "+61 3 9000 0000"}


def test_the_obvious_hours_page():
    html = '<a href="/menu">Menu</a><a href="https://other.example/hours">x</a><a href="/contact-us#map">Contact</a>'
    assert site_read.hours_page(html, URL) == "https://bluefin.example/contact-us"
    assert site_read.hours_page('<a href="/menu">Menu</a>', URL) == ""
