"""exported.log und WooCommerce-Meta verhindern Doppelimporte."""
import woo_to_cdh as w


def test_log_schreiben_und_lesen(tmp_log):
    assert w.load_exported_log() == set()
    w.append_to_exported_log("Shop", 1402, "402", "a.wex")
    w.append_to_exported_log("Shop", 1403, "403", "b.wex")
    assert w.load_exported_log() == {"1402|402", "1403|403"}
    assert tmp_log.read_text(encoding="utf-8").startswith("timestamp\t")


def test_filter_lokal_und_woocommerce(tmp_log):
    w.append_to_exported_log("Shop", 1, "1", "a.wex")

    class Client(w.WooClient):
        def __init__(self):
            self.statuses = ["processing"]

        def _get(self, path, params=None):
            if params.get("page") != 1:
                return []
            return [
                {"id": 1, "number": "1", "meta_data": []},                                   # lokal geloggt
                {"id": 2, "number": "2", "meta_data": [{"key": w.EXPORT_META_KEY, "value": "x"}]},  # Woo-Meta
                {"id": 3, "number": "3", "meta_data": []},                                   # neu
            ]

    neu = [o["number"] for o in Client().iter_new_orders(exported_locally=w.load_exported_log())]
    assert neu == ["3"]
