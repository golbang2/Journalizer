import unittest

from app.services.bank_pdf_parser import ReceiptParseError
from app.services.woori_pdf_parser import parse_woori_pdf
from app.services.journalizer import build_journal_entry


def fixture(blank_vat=False):
    labels=['카드종류','카드번호','거래일시','결제방법','거래금액','자원순환보증금','부가세','봉사료','승인번호','가맹점명','가맹점주소','가맹점번호','사업자번호','연락처']
    values=['마스터카드','5339-****-****-8014','2023/10/24 15:03:28','체크계좌','1,100원','0원','' if blank_vat else '100원','0원','092298','테스트상점','서울','123456789','111-22-33333','02-1234-5678']
    words=[]
    for index,(label,value) in enumerate(zip(labels,values)):
        y=.35+index*.026
        for text,x,right in [(label,.20,.32),(value,.35,.61 if index==1 else .58)]:
            if not text:continue
            words.append({'confidence':.99,'symbols':[{'text':c} for c in text],'boundingBox':{'normalizedVertices':[{'x':x,'y':y-.005},{'x':right,'y':y-.005},{'x':right,'y':y+.005},{'x':x,'y':y+.005}]}})
    # Date is deliberately tokenized as Vision words, rather than a combined timestamp.
    timestamp=words[5]
    timestamp['symbols']=[{'text':c} for c in '2023/10/24']
    timestamp['symbols'][-1]['property']={'detectedBreak':{'type':'SPACE'}}
    timestamp['boundingBox']['normalizedVertices'][1]['x']=.46
    timestamp['boundingBox']['normalizedVertices'][2]['x']=.46
    words.append({'confidence':.99,'symbols':[{'text':c} for c in '15:03:28'],'boundingBox':{'normalizedVertices':[{'x':.47,'y':.397},{'x':.58,'y':.397},{'x':.58,'y':.407},{'x':.47,'y':.407}]}})
    return {'text':'우리카드 매출전표','pages':[{'width':595,'height':841,'blocks':[{'paragraphs':[{'words':words}]}]}]}


class WooriParserTests(unittest.TestCase):
    def test_labeled_cells_amounts_and_leading_zero_approval(self):
        receipt,metadata=parse_woori_pdf(fixture())
        self.assertEqual(str(receipt.purchased_at),'2023-10-24')
        self.assertEqual(str(receipt.total),'1100')
        self.assertEqual(str(receipt.subtotal),'1000')
        self.assertEqual(str(receipt.tax),'100')
        self.assertEqual(receipt.approval_number,'092298')
        self.assertEqual(receipt.merchant,'테스트상점')
        self.assertEqual(metadata['card_last_four'],'8014')
        self.assertFalse(metadata['vat_blank'])

    def test_blank_vat_remains_distinct_from_explicit_zero(self):
        receipt,metadata=parse_woori_pdf(fixture(blank_vat=True))
        self.assertTrue(metadata['vat_blank'])
        self.assertIsNone(metadata['raw_tax'])
        self.assertEqual(str(receipt.tax),'0')
        self.assertEqual(str(receipt.subtotal),'1100')
        self.assertTrue(any('부가세가 공란' in w for w in receipt.warnings))

    def test_check_account_warning_survives_existing_journal_engine(self):
        receipt,_=parse_woori_pdf(fixture())
        draft=build_journal_entry(receipt)
        self.assertEqual(draft.lines[-1].account_code,'21000')
        self.assertTrue(any('실제 결제계좌' in w for w in draft.warnings))
        self.assertEqual(sum(l.amount for l in draft.lines if l.side=='debit'),sum(l.amount for l in draft.lines if l.side=='credit'))

    def test_unknown_document_is_not_forced_into_card_layout(self):
        data=fixture();data['text']='전자세금계산서'
        with self.assertRaises(ReceiptParseError):parse_woori_pdf(data)

    def test_nonzero_deposit_is_not_silently_expensed(self):
        data=fixture()
        words=data['pages'][0]['blocks'][0]['paragraphs'][0]['words']
        words[11]['symbols']=[{'text':c} for c in '10원']
        with self.assertRaises(ReceiptParseError):parse_woori_pdf(data)


if __name__=='__main__':unittest.main()
