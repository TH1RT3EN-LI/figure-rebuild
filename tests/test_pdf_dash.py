"""Analytic lengths, polynomial identity, paint continuity and strict failures."""
import math
import unittest

from figure_rebuild.pdf_dash import PdfDashError,lower_dashes


def bezier(points,t):
    return tuple((1-t)**3*points[0][j]+3*(1-t)**2*t*points[1][j]+3*(1-t)*t*t*points[2][j]+t**3*points[3][j] for j in (0,1))


class PdfDashTests(unittest.TestCase):
    def test_signed_phase_and_odd_patterns_have_analytic_line_positions(self):
        commands=(('M',(0,0)),('L',(14,0)))
        expected={0:[(0,4),(6,10),(12,14)],1:[(0,3),(5,9),(11,14)],-1:[(1,5),(7,11),(13,14)],7:[(0,3),(5,9),(11,14)]}
        for phase,pairs in expected.items():
            with self.subTest(phase=phase):
                r=lower_dashes(commands,[4,2],phase)
                self.assertEqual([(r.commands[i][1][0],r.commands[i+1][1][0]) for i in range(0,len(r.commands),2)],pairs)
        r=lower_dashes((('M',(0,0)),('L',(12,0))),[2,1,3])
        self.assertEqual(r.provenance['normalized_pattern'],[2,1,3,2,1,3])
        self.assertEqual(r.commands,(('M',(0.,0.)),('L',(2.,0.)),('M',(3.,0.)),('L',(6.,0.)),('M',(8.,0.)),('L',(9.,0.))))

    def test_pattern_restarts_only_at_moveto(self):
        r=lower_dashes((('M',(0,0)),('L',(10,0)),('M',(0,10)),('L',(10,10))),[4,2],1)
        self.assertEqual([c[1] for c in r.commands if c[0]=='M'],[(0,0),(5,0),(0,10),(5,10)])
        corner=lower_dashes((('M',(0,0)),('L',(3,0)),('L',(3,4))),[5,1])
        self.assertEqual(corner.commands[:3],(('M',(0,0)),('L',(3,0)),('L',(3,2))))
        self.assertEqual(len(corner.provenance['subpaths'][0]['dash_runs'][0]['source_intervals']),2)

    def test_closed_seam_is_joined_without_extra_caps(self):
        rectangle=(('M',(10,10)),('L',(30,10)),('L',(30,30)),('L',(10,30)),('Z',))
        r=lower_dashes(rectangle,[35,10])
        self.assertEqual(r.commands,(('M',(25.,30.)),('L',(10,30)),('L',(10,10)),('L',(30,10)),('L',(30,25))))
        self.assertTrue(r.provenance['subpaths'][0]['dash_runs'][0]['joins_closed_seam'])
        all_on=lower_dashes(rectangle,[100,10])
        self.assertEqual(all_on.commands[-1],('Z',))
        self.assertTrue(all_on.provenance['subpaths'][0]['dash_runs'][0]['closed'])

    def test_cubic_arc_positions_against_independent_exact_antiderivative(self):
        # This cubic's speed is 300*(1-2t+2t^2), hence S(t)=300t-300t^2+200t^3.
        controls=((0,0),(0,100),(100,100),(100,0))
        r=lower_dashes((('M',controls[0]),('C',*controls[1:])),[21,10],tolerance=1e-4)
        arc=lambda t:300*t-300*t*t+200*t**3
        self.assertFalse(r.provenance['geometry_flattened'])
        self.assertTrue(all(c[0] in ('M','C') for c in r.commands))
        model=r.provenance['subpaths'][0]
        self.assertLessEqual(model['length_lower'],200)
        self.assertGreaterEqual(model['length_upper'],200)
        for i,run in enumerate(model['dash_runs']):
            interval=run['source_intervals'][0]
            self.assertLessEqual(abs(arc(interval['t0'])-31*i),r.provenance['maximum_arc_position_error_upper'])
            self.assertLessEqual(abs(arc(interval['t1'])-min(31*i+21,200)),r.provenance['maximum_arc_position_error_upper'])
            piece=(r.commands[2*i][1],*r.commands[2*i+1][1:])
            # Exact polynomial subcurve identity, not just matching endpoints.
            for k in range(11):
                u=k/10
                t=interval['t0']+(interval['t1']-interval['t0'])*u
                self.assertLess(math.dist(bezier(piece,u),bezier(controls,t)),1e-10)

    def test_per_interval_receipt_covers_both_endpoint_errors(self):
        r=lower_dashes((("M",(0,0)),("C",(0,100),(100,100),(100,0))),[5,7])
        arc=lambda t:300*t-300*t*t+200*t**3
        for run in r.provenance["subpaths"][0]["dash_runs"]:
            for interval in run["source_intervals"]:
                for key,distance in [("t0","arc_start_estimate"),("t1","arc_end_estimate")]:
                    self.assertLessEqual(abs(arc(interval[key])-interval[distance]),interval["arc_position_error_upper"]+r.provenance["floating_point_allowance"])

    def test_collinear_cubic_uses_arc_length_not_linear_parameter(self):
        r=lower_dashes((('M',(0,0)),('C',(0,0),(0,0),(100,0))),[10,10])
        first=r.provenance['subpaths'][0]['dash_runs'][0]['source_intervals'][0]
        self.assertAlmostEqual(first['t1'],.1**(1/3),places=5)
        self.assertAlmostEqual(r.commands[1][-1][0],10,places=4)

    def test_closed_curve_with_ambiguous_seam_is_rejected(self):
        with self.assertRaisesRegex(PdfDashError,'cap/join topology'):
            lower_dashes((('M',(0,0)),('C',(0,100),(100,100),(100,0)),('Z',)),[10,10])

    def test_zero_patterns_degenerate_paths_and_resource_budgets_fail_closed(self):
        path=(('M',(0,0)),('L',(100,0)))
        for pattern in ([],[0,1],[1,0],[-1,2],[float('nan'),1]):
            with self.subTest(pattern=pattern),self.assertRaises(PdfDashError):lower_dashes(path,pattern)
        for commands in [(('M',(0,0)),),(('M',(0,0)),('L',(0,0))),(('M',(0,0)),('C',(0,0),(0,0),(0,0)))]:
            with self.subTest(commands=commands),self.assertRaises(PdfDashError):lower_dashes(commands,[1,1])
        with self.assertRaisesRegex(PdfDashError,'command budget'):
            lower_dashes(path,[1,1],max_output_commands=3)
        with self.assertRaisesRegex(PdfDashError,'subdivision budget'):
            lower_dashes((('M',(0,0)),('C',(0,100),(100,100),(100,0))),[1,1],max_arc_leaves=2)
        with self.assertRaisesRegex(PdfDashError,'floating-point allowance'):
            lower_dashes((('M',(1e12,0)),('L',(1e12+100,0))),[10,10])

    def test_tiny_nonzero_gap_cannot_be_snapped_into_corner_join(self):
        square=(('M',(0,0)),('L',(1,0)),('L',(1,1)),('L',(0,1)),('Z',))
        with self.assertRaisesRegex(PdfDashError,'cap/join topology'):
            lower_dashes(square,[4-1e-14,1])
        corner=(('M',(0,0)),('L',(1,0)),('L',(1,1)))
        with self.assertRaisesRegex(PdfDashError,'cap/join topology'):
            lower_dashes(corner,[1+1e-14,1])

    def test_integer_rectangle_phase_stays_exact_at_corners(self):
        # Large integer source units are normal in Office-authored PDFs.
        p=(('M',(0,0)),('L',(11671300,0)),('L',(11671300,4381500)),('L',(0,4381500)),('Z',))
        r=lower_dashes(p,[12700,12700],tolerance=3.)
        runs=r.provenance['subpaths'][0]['dash_runs']
        self.assertEqual(len(runs),1264)
        self.assertEqual(len(r.commands),2528)
        self.assertEqual(r.provenance['maximum_floating_boundary_snap'],0)
        self.assertEqual(r.provenance['subpaths'][0]['length_estimate'],32105600)


if __name__=='__main__':unittest.main()
