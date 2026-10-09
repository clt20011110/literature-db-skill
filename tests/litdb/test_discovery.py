from __future__ import annotations

import unittest
from unittest.mock import patch

import test_search as fixtures
from litdb.discovery import discover, validate_options
from litdb.search import SearchError, export_catalog, markdown_results, search_home


class FakeKev:
    def __init__(self, probabilities=None):
        self.probabilities=probabilities or {}
        self.calls=[]

    def rerank(self, topic, papers):
        self.calls.append((topic,papers))
        results=[]
        for p in papers:
            probs=dict(zip(('direct','background','unrelated'),self.probabilities.get(p['title'],[0.8,0.15,0.05])))
            results.append(dict(paper_id=p['id'],choice=max(probs,key=probs.get),probabilities=probs))
        return dict(results=results,model='fake-kev',endpoint='test')


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.SearchTests()
        self.fixture.setUp()
        self.paths=self.fixture.paths

    def tearDown(self):
        self.fixture.tearDown()

    def seed(self, titles):
        for i,(title,abstract) in enumerate(titles):
            key=f'icml:{i}'
            self.fixture.canonical(key,title=title,abstract=abstract,source_id=key,year=2020)
            self.fixture.source(key,source_id=key,year=2020)
        export_catalog(self.paths,search_home(self.paths))
        self.fixture.write_ready_state()
        return [self.fixture.lookup_paper(f'icml:{i}') for i in range(len(titles))]

    def test_combined_deduplicates_then_filters_and_reranks_with_uncertainty(self):
        papers=self.seed([('Direct','graph forecasting'),('Background','graph foundation'),
                          ('Reject','graph words but different task'),('Uncertain','graph ambiguous')])
        bridge=fixtures.FakeBridge([self.fixture.hit(papers[0][1]),self.fixture.hit(papers[1][1])])
        kev=FakeKev({'Direct':[.8,.1,.1],'Background':[.1,.8,.1],
                     'Reject':[.01,.04,.95],'Uncertain':[.33,.33,.34]})
        before=self.paths.catalog.read_bytes()
        result=discover(self.paths,dict(query='图神经网络预测交通',retrieval_queries=['graph traffic prediction'],
                          keywords=['graph'],candidate_limit=10),bridge=bridge,client=kev)
        self.assertEqual(len(kev.calls),1)
        self.assertEqual(kev.calls[0][0],'图神经网络预测交通')
        self.assertEqual(len(kev.calls[0][1]),4)
        self.assertEqual([p['title'] for p in result['results']],['Direct','Background','Uncertain'])
        self.assertTrue(result['results'][-1]['relevance']['uncertain'])
        self.assertEqual(result['filtered'][0]['title'],'Reject')
        self.assertEqual(result['counts']['retrieved_unique'],4)
        self.assertEqual(result['counts']['reranked'],4)
        self.assertEqual(len(result['results'][0]['retrieved_by']),3)
        self.assertEqual(len(bridge.requests),2)
        self.assertEqual(self.paths.catalog.read_bytes(),before)
        self.assertIn('待核验',markdown_results(result))

    def test_candidate_budget_applies_before_inference(self):
        papers=self.seed([(f'Graph {i}','graph traffic') for i in range(8)])
        bridge=fixtures.FakeBridge([self.fixture.hit(path) for _,path in papers])
        kev=FakeKev()
        result=discover(self.paths,dict(query='graph traffic',keywords=['graph'],limit=2,candidate_limit=3),bridge,kev)
        self.assertEqual(len(kev.calls[0][1]),3)
        self.assertEqual(len(result['results']),2)
        self.assertEqual(bridge.requests[0]['limit'],3)
        self.assertLessEqual(result['counts']['reranked'],3)

    def test_keyword_only_never_starts_zvec_and_preserves_alternate_year_scope(self):
        papers=self.seed([('Direct','graph traffic'),('Other','graph traffic')])
        self.fixture.source('icml:0',source_id='alternate',year=2019)
        export_catalog(self.paths,search_home(self.paths));self.fixture.write_ready_state()
        kev=FakeKev()
        with patch('litdb.discovery.ZvecBridge',side_effect=AssertionError('No embedding runtime')):
            result=discover(self.paths,dict(query='graph',keywords=['graph'],retriever='keyword',
                                          year_from=2019,year_to=2019),client=kev)
        self.assertEqual([p['id'] for p in result['results']],[papers[0][0]['id']])
        self.assertEqual(result['eligible_papers'],1)
        self.assertEqual(result['results'][0]['matched_editions'],[{'id':'icml','year':2019}])
        self.assertIn('ICML 2019（主记录：ICML 2020）',markdown_results(result))

    def test_no_candidates_does_not_call_kev_and_is_not_full_catalog_claim(self):
        self.seed([('Graph','graph traffic')]);kev=FakeKev()
        result=discover(self.paths,dict(query='organic chemistry',keywords=['palladium'],retriever='keyword'),client=kev)
        self.assertEqual(kev.calls,[])
        self.assertEqual(result['counts']['reranked'],0)
        self.assertIn('不代表全库',markdown_results(result))

    def test_kev_failure_does_not_return_retrieval_results_as_reranked(self):
        self.seed([('Graph','graph traffic')]);kev=FakeKev()
        with patch.object(kev,'rerank',side_effect=SearchError('Unavailable')):
            with self.assertRaisesRegex(SearchError,'Unavailable'):
                discover(self.paths,dict(query='graph',keywords=['graph'],retriever='keyword'),client=kev)

    def test_missing_abstract_marks_title_only_decision_uncertain(self):
        self.seed([('Graph','   \n\t')])
        result=discover(self.paths,dict(query='graph',keywords=['graph'],retriever='keyword'),
                        client=FakeKev({'Graph':[0.01,0.01,0.98]}))
        self.assertEqual(result['results'][0]['evidence_scope'],'title_only')
        self.assertTrue(result['results'][0]['relevance']['uncertain'])

    def test_unknown_venue_rejected_even_in_keyword_mode(self):
        self.seed([('Graph','graph traffic')])
        with self.assertRaisesRegex(ValueError,'venue'):
            discover(self.paths,dict(query='graph',keywords=['graph'],retriever='keyword',venues=['missing']),client=FakeKev())

    def test_bad_options_are_rejected_before_retrieval(self):
        base=dict(query='graph',keywords=['graph'])
        for override in ({'keywords':[]},{'candidate_limit':True},{'candidate_limit':501},
                         {'candidate_limit':1,'limit':2},{'unrelated_threshold':float('nan')},
                         {'unrelated_threshold':.4},{'retrieval_queries':['']},
                         {'retrieval_queries':['x']*6},{'keywords':['a\x00b']},
                         {'retriever':'remote'},{'keywords':'graph'}):
            with self.subTest(override=override),self.assertRaises(ValueError):
                validate_options({**base,**override})
        valid=validate_options(dict(query='graph',retriever='zvec',retrieval_queries=['graph','graphs']))
        self.assertEqual(valid['retrieval_queries'],['graph','graphs'])

    def test_bridge_is_closed_if_recall_fails(self):
        self.seed([('Graph','graph traffic')])
        with patch('litdb.discovery.ZvecBridge') as factory:
            factory.return_value.call.side_effect=SearchError('Failed retrieval')
            with self.assertRaises(SearchError):
                discover(self.paths,dict(query='graph',retriever='zvec'),client=FakeKev())
            factory.return_value.close.assert_called_once()


if __name__=='__main__':unittest.main()
