import unittest

from app.services.bank_pdf_parser import ReceiptParseError
from app.services.card_receipt_parser import parse_card_receipt


def fixture(tax=None, subtotal=None):
    rows = [('거래일시','2023/09/08 16:19:18'),('금액','2,000'),
            ('승인번호','03172589'),('할부개월','일시불'),
            ('사업자번호','111-22-33333'),('납부번호','001-03172589')]
    if tax is not None:
        rows.append(('부가세',tax))
    if subtotal is not None:
        rows.append(('공급가액',subtotal))
    words=[]

    def word(text,x,y,right):
        symbols=[{'text':c} for c in text]
        symbols[-1]['property']={'detectedBreak':{'type':'SPACE'}}
        return {'symbols':symbols,'confidence':.99,'boundingBox':{'normalizedVertices':[
            {'x':x,'y':y-.01},{'x':right,'y':y-.01},
            {'x':right,'y':y+.01},{'x':x,'y':y+.01}]}}

    words.append(word('테스트등기국',.12,.14,.35))
    words.extend([word('주소',.12,.18,.30),word('서울',.42,.18,.62)])
    for i,(label,value) in enumerate(rows):
        y=.24+i*.03
        words.extend([word(label,.12,y,.30),word(':',.32,y,.34),word(value,.42,y-.005,.64)])
    return {'text':'비씨카드 승인\n[신용승인]', 'pages':[{'width':800,'height':1200,
            'blocks':[{'paragraphs':[{'words':words}]}]}]}


class CardReceiptParserTests(unittest.TestCase):
    def test_header_date_amount_and_leading_zero_approval_with_adjacent_rows(self):
        receipt=parse_card_receipt(fixture())
        self.assertEqual(receipt.merchant,'테스트등기국')
        self.assertEqual(str(receipt.purchased_at),'2023-09-08')
        self.assertEqual(str(receipt.total),'2000')
        self.assertEqual(receipt.approval_number,'03172589')
        self.assertEqual(receipt.business_number,'111-22-33333')
        self.assertEqual(receipt.payment_method,'신용카드')
        self.assertTrue(any('0원' in w for w in receipt.warnings))
        self.assertTrue(any('주소 위' in w for w in receipt.warnings))

    def test_explicit_vat_reconciles_and_mismatched_subtotal_fails(self):
        receipt=parse_card_receipt(fixture(tax='200',subtotal='1,800'))
        self.assertEqual(str(receipt.tax),'200')
        self.assertEqual(str(receipt.subtotal),'1800')
        self.assertFalse(any('초안 세액을 0원' in w for w in receipt.warnings))
        with self.assertRaises(ReceiptParseError):
            parse_card_receipt(fixture(tax='200',subtotal='1,900'))

    def test_missing_approval_low_amount_confidence_and_duplicate_receipts_fail(self):
        for mode in ('approval','confidence','duplicates'):
            data=fixture();words=data['pages'][0]['blocks'][0]['paragraphs'][0]['words']
            if mode=='approval':
                words[:]=[w for w in words if ''.join(s['text'] for s in w['symbols'])!='승인번호']
            elif mode=='confidence':
                next(w for w in words if ''.join(s['text'] for s in w['symbols'])=='2,000')['confidence']=.4
            else:
                words.extend(fixture()['pages'][0]['blocks'][0]['paragraphs'][0]['words'])
            with self.subTest(mode=mode), self.assertRaises(ReceiptParseError):
                parse_card_receipt(data)


if __name__=='__main__':
    unittest.main()
