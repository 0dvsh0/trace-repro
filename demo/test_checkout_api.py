import urllib.request
import urllib.parse
import http.cookiejar
import re

cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

print("1. Adding basket...")
opener.open('http://127.0.0.1:8000/demo/basket/').read()

print("2. Getting checkout page...")
resp = opener.open('http://127.0.0.1:8000/checkout/')
html = resp.read().decode('utf-8')
csrf = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', html).group(1)

print("3. Posting guest email...")
data = urllib.parse.urlencode({
    'csrfmiddlewaretoken': csrf,
    'username': 'devansh@example.com',
    'options': 'anonymous'
}).encode('utf-8')
req = urllib.request.Request('http://127.0.0.1:8000/checkout/', data=data)
resp = opener.open(req)
html = resp.read().decode('utf-8')
print("Current URL after guest email:", resp.geturl())

# Let's see what form is on this page:
for m in re.finditer(r'<form\s+.*?method=["\']post["\'].*?</form>', html, re.DOTALL | re.IGNORECASE):
    f = m.group(0)
    if 'setlang' not in f:
        print("FOUND FORM ON NEXT PAGE:")
        print(f[:500])

csrf_match = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', html)
if csrf_match:
    csrf = csrf_match.group(1)
    print("\n4. Submitting payment form (placing order)...")
    data = urllib.parse.urlencode({'csrfmiddlewaretoken': csrf, 'action': 'place_order'}).encode('utf-8')
    req = urllib.request.Request(resp.geturl(), data=data)
    try:
        resp = opener.open(req)
        print("STATUS:", resp.getcode())
        print("URL:", resp.geturl())
    except urllib.error.HTTPError as e:
        print("CAUGHT HTTP ERROR:", e.code)
        err_body = e.read().decode('utf-8')
        if "We couldn't take your payment" in err_body:
            print("SUCCESS! RENDERED: We couldn't take your payment")
        ref = re.search(r'Order reference: ([^\s·<]+)', err_body)
        if ref:
            print("ORDER REFERENCE:", ref.group(1))
        status_match = re.search(r'status: ([^\s<]+)', err_body)
        if status_match:
            print("STATUS:", status_match.group(1))
