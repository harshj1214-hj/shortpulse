"""'Closes at exactly N clicks, even for simultaneous visitors.'

Starts a real multi-threaded HTTP server and fires many requests at a capped
link at the same moment (a Barrier releases all threads together).
"""
import http.client
import threading
import unittest

from werkzeug.serving import make_server

from helpers import BROWSER_UA, AppTestCase


class ClickLimitRaceTest(AppTestCase):
    def test_exactly_n_redirects_under_contention(self):
        self.login(self.user_id)
        for alias, cap in (("race5", 5), ("race1", 1)):
            self.assertEqual(self.create_link(alias=alias, max_clicks=cap).status_code, 201)

        server = make_server("127.0.0.1", 0, self.app, threaded=True)
        port = server.server_port
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        try:
            for alias, cap, visitors in (("race5", 5, 60), ("race1", 1, 40)):
                barrier = threading.Barrier(visitors)
                results = []
                lock = threading.Lock()

                def visit():
                    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
                    barrier.wait()
                    conn.request("GET", f"/{alias}", headers={"User-Agent": BROWSER_UA})
                    status = conn.getresponse().status
                    conn.close()
                    with lock:
                        results.append(status)

                threads = [threading.Thread(target=visit) for _ in range(visitors)]
                for th in threads:
                    th.start()
                for th in threads:
                    th.join()
                with self.subTest(alias=alias):
                    self.assertEqual(results.count(302), cap, results)
                    self.assertEqual(results.count(410), visitors - cap, results)
                    count = self.one("SELECT click_count FROM links WHERE code = ?", (alias,))[0]
                    self.assertEqual(count, cap)
        finally:
            server.shutdown()
        self.flush()
        self.assertEqual(self.one("SELECT COUNT(*) FROM click_events")[0], 6)


if __name__ == "__main__":
    unittest.main()
