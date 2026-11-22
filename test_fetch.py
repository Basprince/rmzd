import sys, os, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import fetch_all as F

BOC = """<table><tr><th>货币名称</th><th>现汇买入价</th><th>现钞买入价</th><th>现汇卖出价</th><th>现钞卖出价</th><th>中行折算价</th><th>发布时间</th></tr>
<tr><td>美元</td><td>664.12</td><td>664.12</td><td>668.71</td><td>668.71</td><td>666.5</td><td>2026.11.06 15:54:26</td></tr>
<tr><td>澳大利亚元</td><td>455.1</td><td>441.2</td><td>461.10</td><td>461.10</td><td>458</td><td>2026.11.06 15:54:26</td></tr>
<tr><td>新西兰元</td><td>368.2</td><td>357</td><td>372.27</td><td>372.27</td><td>370</td><td>2026.11.06 15:54:26</td></tr>
<tr><td>日元</td><td>4.1</td><td>4.0</td><td>4.3</td><td>4.3</td><td>4.2</td><td>2026.11.06 15:54:26</td></tr></table>"""
CEPEA = """<table id="imagenet-indicador1"><tr><th>Data</th><th>Valor R$</th><th>Variação</th><th>Valor US$</th></tr>
<tr><td>03/11/2026</td><td>390,00</td><td>0,10%</td><td>71,10</td></tr><tr><td>30/10/2026</td><td>389,90</td><td>0,20%</td><td>71,50</td></tr></table>"""


class T(unittest.TestCase):
    def test_boc(self):
        r = F.parse_boc(BOC)
        self.assertEqual(r["USD"], {"rate": 6.6871, "date": "2026-11-06"})
        self.assertEqual(r["AUD"]["rate"], 4.611)
        self.assertEqual(r["NZD"]["rate"], 3.7227)
        self.assertNotIn("日元", r)

    def test_cepea(self):
        self.assertEqual(F.parse_cepea(CEPEA), {"date": "2026-11-03", "brl": 390.0, "usd": 71.1})

    def test_au(self):
        v = F.parse_au("EYCI 894.12c/kg cwt, NYCI sits at 488.4c, Light Lamb indicator 1076.16c/kg, Heavy Lamb 1103.85c")
        self.assertEqual(v, {"EYCI": 894.12, "NYCI": 488.4, "LIGHT": 1076.16, "HEAVY": 1103.85})

    def test_num_date(self):
        self.assertEqual(F.num("1.234,50"), 1234.5)
        self.assertEqual(F.num("7,500.00"), 7500.0)
        self.assertEqual(F.to_date("03/11/2026"), "2026-11-03")
        self.assertEqual(F.to_date("2026.11.06 15:54"), "2026-11-06")

    def test_upsert(self):
        l = [{"date": "2026-11-06", "rate": 1}]
        F.upsert(l, "date", {"date": "2026-11-06", "rate": 2})
        F.upsert(l, "date", {"date": "2026-11-07", "rate": 3})
        self.assertEqual(l, [{"date": "2026-11-06", "rate": 2}, {"date": "2026-11-07", "rate": 3}])


if __name__ == "__main__":
    unittest.main(verbosity=2)
