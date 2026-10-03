"""Conservative stroke support in transformed coordinates.

This bounds declared SVG stroke geometry, not a PDF device-space hairline.
The centerline bounds must already be in the matrix's output coordinates;
the returned Fractions expand those bounds on x/y. Round/bevel joins and
butt/round caps stay inside the radius width/2 disk around the centerline.
After an arbitrary affine transform, that disk's axis supports are the row
L2 norms times the radius. A square cap adds perpendicular tangent/normal
offsets, so its radius is at most sqrt(2) times the half-width.

All square roots are rounded upward using exact integer arithmetic. No
near-similarity or floating epsilon establishes a geometric containment proof.
Miter joins preserve the previous, deliberately generous row-L1 bound. The
rectangle flag is a caller certificate of a single closed, axis-aligned
rectangle in OUTPUT coordinates, and tightens miter bounds only when the
matrix is an exact nondegenerate similarity over its input values.
"""
from fractions import Fraction
import math


class PdfStrokeBoundsError(ValueError):
    """Invalid or unsupported input; no bound has been established."""


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PdfStrokeBoundsError("Stroke bounds require finite numeric values")
    try:
        if not math.isfinite(value):
            raise PdfStrokeBoundsError("Stroke bounds require finite numeric values")
    except OverflowError as error:
        raise PdfStrokeBoundsError("Stroke bounds exceed finite input values") from error
    return Fraction(value)


def _power_two(exponent):
    return Fraction(1 << exponent) if exponent >= 0 else Fraction(1, 1 << -exponent)


def _sqrt_upper(value):
    """An exact or upward dyadic bound with at most 53 significant bits.

    Integer isqrt and a squared rational comparison determine the rounding
    direction, including subnormal-scale values and squared values larger
    than the floating-point range. The return value is always a Fraction.
    """
    if value == 0:
        return Fraction(0)
    exponent = (value.numerator.bit_length()-value.denominator.bit_length())//2
    if _power_two(2*exponent) > value:
        exponent -= 1
    step = _power_two(exponent-52)
    scaled = value/(step*step)
    root = math.isqrt(scaled.numerator//scaled.denominator)
    if root*root*scaled.denominator < scaled.numerator:
        root += 1
    return root*step


def stroke_envelope(matrix, width, miter_limit, rectangle=False, *,
                    linecap="butt", linejoin="miter"):
    """Return outward Fraction x/y supports for one declared SVG stroke.

    Four positional arguments preserve the previous helper's calling shape.
    Explicit styles allow a tighter round/bevel bound. ``rectangle=True``
    requires the caller's closed output-axis rectangle proof; it is not a
    tolerance-based geometric guess. Width zero has SVG zero-width semantics
    and returns zero supports. Unknown styles, negative width and nonfinite
    inputs raise ``PdfStrokeBoundsError``. Coordinates are never modified.
    """
    if not isinstance(matrix, (list, tuple)) or len(matrix) != 6:
        raise PdfStrokeBoundsError("Stroke matrix requires six finite values")
    values = tuple(_number(v) for v in matrix)
    width, miter = _number(width), _number(miter_limit)
    if width < 0 or miter < 1:
        raise PdfStrokeBoundsError("Stroke width must be nonnegative and miter limit at least one")
    if type(rectangle) is not bool:
        raise PdfStrokeBoundsError("Rectangle certificate must be a boolean")
    if linecap not in ("butt", "round", "square") or linejoin not in ("miter", "round", "bevel"):
        raise PdfStrokeBoundsError("Unsupported stroke cap or join")
    if width == 0:
        return Fraction(0), Fraction(0)
    a, b, c, d = values[:4]
    if linejoin in ("round", "bevel"):
        multiplier = 2 if linecap == "square" else 1
        return (width*_sqrt_upper(multiplier*(a*a+c*c))/2,
                width*_sqrt_upper(multiplier*(b*b+d*d))/2)
    squared = a*a+b*b
    if rectangle and squared > 0 and squared == c*c+d*d and a*c+b*d == 0:
        return (width*_sqrt_upper(squared)/2,)*2
    # Retain the legacy miter bound, also safely enclosing square caps.
    factor = width*max(Fraction(2), miter/2)
    return factor*(abs(a)+abs(c)), factor*(abs(b)+abs(d))
