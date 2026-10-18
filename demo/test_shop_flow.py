import urllib.request
import http.cookiejar
import re

cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

# Reset shop
opener.open('http://127.0.0.1:8000/shop-reset/')

# Add to basket
opener.open('http://127.0.0.1:8000/demo/basket/')

# Checkout guest
data = urllib.parse.urlencode({
    'username': 'devansh@example.com',
    'options': 'guest',
    'action': 'guest'
}).encode()
req = urllib.request.Request('http://127.0.0.1:8000/checkout/', data=data)
resp = opener.open(req)
print("After guest checkout URL:", resp.geturl())

# Let's get csrf token from payment details
resp = opener.open('http://127.0.0.1:8000/checkout/payment-details/')
html = resp.read().decode('utf-8')
csrf = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', html).group(1)
print("CSRF token found:", csrf[:10])

# Post payment
pay_data = urllib.parse.urlencode({
    'csrfmiddlewaretoken': csrf,
    'action': 'place_order'
}).encode()
pay_req = urllib.request.Request('http://127.0.0.1:8000/checkout/payment-details/', data=pay_data)
try:
    pay_resp = opener.open(pay_req)
    print("Payment response URL:", pay_resp.geturl(), "status:", pay_resp.status)
    print(pay_resp.read().decode('utf-8')[:300])
except urllib.error.HTTPError as e:
    print("Payment HTTP error:", e.code)
    print(e.read().decode('utf-8')[:300])
