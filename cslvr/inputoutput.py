from scipy.io          import loadmat
from scipy.interpolate import RectBivariateSpline, griddata, interp2d, interp1d
from numpy             import array, linspace, ones, isnan, all, zeros, shape, \
                              ndarray, e, nan, float64, logical_and, where, \
                              meshgrid, arange
from dolfin            import interpolate, Expression, Function, \
                              vertices, FunctionSpace, RectangleMesh, \
                              MPI, mpi_comm_world, GenericVector, parameters, \
                              File, Constant, FiniteElement
from ufl               import indexed
from pyproj            import Proj, transform
from colored           import fg, attr

class DataInput(object):
  """
  This class is used as a container for all geographical and geological data
  used by CSLVR, and posseses the ability to

  1. Remove rows or columns in sets that are entirely composed of ``nan``'s (done automatically).
  2. Convert projections between datasets.
  3. Generate :class:`~dolfin.Expression` objects to be used by :class:`~model.Model` instances
  
  :param di:         dictionary of data returned by one of the functions of :class:`~datafactory.DataFactory`
  :param mesh:       finite-element mesh to use for generation of :class:`~dolfin.Expression`'s, if desired
  :param order:      if a mesh is used, this defines the order of the finite-element basis used by :func:`~inputoutput.DataInput.get_expression`
  :type di: dict
  :type mesh: :class:`~dolfin.Mesh`
  :type order: int

  A number of class variables are generated by the instantiation of this 
  class which may be useful.  These are:

  * ``self.data`` -- dict of data associated with :class:`~datafactory.DataFactory` dict ``di``
  * ``self.proj`` -- projection :class:`~pyproj.Proj`

  Data projection :math:`x,y` coordinates :

  * ``self.x`` -- :class:`~numpy.array` of data grid :math:`x`-coordinates
  * ``self.y`` -- :class:`~numpy.array` of data grid :math:`y`-coordinates
  """
  def __init__(self, di, mesh=None, order=1):
    """
    """
    self.mesh       = mesh
    self.data       = {}        # dictionary of data
    self.rem_nans   = False     # may change depending on 'identify_nans' call
    self.chg_proj   = False     # change to other projection flag
    self.color      = 'light_green'

    di              = di.copy()

    self.name       = di.pop('dataset')
    self.cont       = di.pop('continent')
    self.proj       = di.pop('pyproj_Proj')

    # initialize extents :
    self.ny         = di.pop('ny')
    self.nx         = di.pop('nx')
    self.dx         = di.pop('dx')
    self.x_min      = float(di.pop('map_western_edge'))
    self.x_max      = float(di.pop('map_eastern_edge'))
    self.y_min      = float(di.pop('map_southern_edge'))
    self.y_max      = float(di.pop('map_northern_edge'))
    self.x          = linspace(self.x_min, self.x_max, self.nx)
    self.y          = linspace(self.y_min, self.y_max, self.ny)
    self.good_x     = array(ones(len(self.x)), dtype=bool)      # no NaNs
    self.good_y     = array(ones(len(self.y)), dtype=bool)      # no NaNs

    s    = "::: creating %s DataInput object :::" % self.name
    print_text(s, self.color)

    # process the data di :
    for fn in di:

      # raw data matrix with key fn :
      d = di[fn]

      # identify, but not remove the NaNs :
      self.__identify_nans(d, fn)

      # add to the dictionary of arrays :
      self.data[fn.split('.')[0]] = d

    # remove un-needed rows/cols from data:
    if self.rem_nans: self.__remove_nans()

    if self.mesh != None:

      # define the finite elmenet of the problem :
      self.element = FiniteElement("CG", self.mesh.ufl_cell(), order)

      self.mesh.init(1,2)
      self.dim        = self.mesh.ufl_cell().topological_dimension()
      if self.dim == 3:
        self.num_facets   = self.mesh.num_facets()
        self.num_cells    = self.mesh.num_cells()
        self.num_vertices = self.mesh.num_vertices()
      elif self.dim == 2:
        self.num_facets   = self.mesh.num_edges()
        self.num_cells    = self.mesh.num_cells()
        self.num_vertices = self.mesh.num_vertices()
      s = "    - using %iD mesh with %i cells, %i facets, %i vertices - " \
          % (self.dim, self.num_cells, self.num_facets, self.num_vertices)
      print_text(s, self.color)
    else:
      s = "    - not using a mesh - "
      print_text(s, self.color)

  def reduce_size_to_box(self, ll_lon, ll_lat, ur_lon, ur_lat):
    """
    Reduce the size of the data to the box with lower-left-corner 
    coordinates (``ll_lon``, ``ll_lat``) and upper-right-corner
    coordinates (``ur_lon``, ``ur_lat``).
    """
    # convert to projection coordinates :
    xmax,ymax = self.proj(ll_lon, ll_lat)
    xmin,ymin = self.proj(ur_lon, ur_lat)

    # mark the areas we want to keep :    
    xlt = self.x < xmin
    xgt = self.x > xmax
    ylt = self.y < ymin
    ygt = self.y > ymax
    
    # take the union :
    xs  = where(logical_and(xlt, xgt))[0]
    ys  = where(logical_and(ylt, ygt))[0]
   
    # cut out the parts we do not need :
    for i in self.data:
      self.data[i] = self.data[i][:,xs]
      self.data[i] = self.data[i][ys,:]

    # replace the statistics :
    self.x     = self.x[xs]
    self.y     = self.y[ys]
    self.x_min = self.x.min()
    self.x_max = self.x.max()
    self.y_min = self.y.min()
    self.y_max = self.y.max()
    self.nx    = len(self.x)
    self.ny    = len(self.y)

  def change_projection(self, di):
    """
    Changes the :class:`~pyproj.Proj` of this data to that of the 
    ``di`` :class:`~inputoutput.DataInput` object's :class:`~pyproj.Proj`.
    The works only if the object was created with the parameter
    ``create_proj == True``.

    :param di: the :class:`~inputoutput.DataInput` which contains the projection you would like to convert to
    """
    if type(di) == type(self):
      proj = di.proj
      name = di.name
    elif type(di) == dict:
      name = di['dataset']
      proj = di['pyproj_Proj']

    s    = "::: changing '%s' DataInput object projection to that of '%s' :::" \
           % (self.name, name)
    print_text(s, self.color)

    self.chg_proj = True
    self.new_p    = proj

  def get_xy(self,lon,lat):
    """
    Returns the :math:`(x,y)` projection map coordinates corresponding to a 
    given (lon,lat) coordinate pair using this :class:`~inputoutput.DataInput`
    object's current projection ``self.proj``.

    :param lon: longitude coordinate(s)
    :param lat: latitude coordinate(s)
    :type lon: float, :class:`~numpy.array`
    :type lat: float, :class:`~numpy.array`
    """
    return self.proj(lon,lat)

  def interpolate_from_di(self, di, fi, fo, order=3):
    """
    Interpolates the data field with key ``fi`` of 
    another :class:`~inputoutput.DataInput` object ``di`` to the grid used 
    by this object.  Saves the resulting interpolation within this object's
    ``self.data`` dictionary with key ``fo``.  Order may be any of:

    * ``1``  -- nearest-neighbor interpolation
    * ``2``  -- linear interpolation
    * ``3``  -- cubic interpolation

    :param di: the :class:`~inputoutput.DataInput` to interpolate from
    :param fi: the key of the data to interpolate from.
    :param fo: the key to save the data within ``self.data``
    :param order: order of interpolation (see above).
    :type di:    :class:`~inputoutput.DataInput`
    :type fi:    string
    :type fo:    string
    :type order: int
    """
    if   order == 1:  method = 'nearest'
    elif order == 2:  method = 'linear'
    elif order == 3:  method = 'cubic'
    else:
      s = ">>> interpolate_from_di() REQUIRES method == 1,2, or 3 <<<"
      print_text(s, 'red', 1)
      sys.exit(1)

    s = "::: interpolating %s's '%s' field to %s's grid with key '%s' using" + \
        " %s interpolation :::"
    print_text(s % (di.name, fi, self.name, fo, method), self.color)

    # check if the projections are the same :
    
    # if they are, then use structured grid interpolation :
    if self.proj.srs == di.proj.srs:
      s      = '    - projections match, using structured interpolation -'
      print_text(s, self.color)
     
      # NOTE the data is transposed as required by RectBivariateSpline.
      #      It must therefore be transposed back, as follows : 
      interp = RectBivariateSpline(di.x, di.y, di.data[fi].T,
                                     kx=order, ky=order)
      fo_v  = interp(self.x, self.y).T

    # if not, then use scipy::griddata to interpolate unstructured data :
    else:
      s      = '    - projections do not match, using unstructured ' + \
               'interpolation -'
      print_text(s, self.color)
      xs,ys   = self.transform_xy(di)
      di_pts  = (xs.flatten(), ys.flatten())
      xr,yr   = meshgrid(self.x, self.y)
      do_pts  = (xr,yr)
      
      # create interpolation object to convert to bedmap2 coordinates :
      # surface accumulation/ablation :
      fo_v    = griddata(di_pts, di.data[fi].flatten(), do_pts,
                         method=method, fill_value=0.0)

    # set the data to our dictionary :
    self.data[fo] = fo_v

    print_min_max(di.data[fi], 'original %s    ' % fi)
    print_min_max(fo_v,        'interpolated %s' % fo)

  def transform_xy(self, di):
    """
    Transforms the projection coordinates (``di.x``, ``di.y``) of the 
    :class:`~inputoutput.DataInput` object ``di`` to the projection used
    by this :class:`~inputoutput.DataInput`'s.
    
    :param di: the :class:`~inputoutput.DataInput` with projection coordinates to transform
    :rtype: tuple of converted coordinates
    """
    # FIXME : need a fast way to convert all the x, y. Currently broken
    s = "::: transforming coordinates from %s to %s :::" % (di.name, self.name)
    print_text(s, self.color)
    vx,vy  = meshgrid(di.x, di.y)
    xn, yn = transform(di.proj, self.proj, vx, vy)
    print_text('    - done -', self.color)
    return (xn, yn)

  def rescale_field(self, fo, fn, umin, umax, inverse=False):
    """
    Rescale the data field of this instance with key ``fo`` to lower and 
    upper bound ``umin``, ``umax``, creating a new data field in the process
    with key ``fn``.

    If ``inverse == True``, scale the data to the inverse of data ``fo``,
    i.e., the smallest values become ``umax``, and the largest become ``umin``.

    This is useful, for example, when refining a mesh in areas where a 
    velocity field is of highest magnitude.

    :param fo: key of the data to rescale
    :param fn: key of the new rescaled data
    :param umin: minimum value to rescale
    :param umax: maximum value to rescale
    :param inverse: rescale the inverse of ``fo`` instead
    :type fo: string
    :type fn: string
    :type umin: float
    :type umax: float
    :type inverse: bool
    """
    if inverse:
      inv_txt = 'inversely'
    elif not inverse:
      inv_txt = ''
    s = "::: rescaling data field '%s' %s with lower and upper " + \
        "bound (%g, %g) to field '%s' :::" 
    print_text(s % (fo, inv_txt, umin, umax, fn), self.color)

    U = self.data[fo]
    if not inverse:
      amin = ( umin/(1.0 + U.max()) - umax/(1.0 + U.min()) ) / (umax - umin)
      amax = umin / ( amin + 1.0/(1.0 + U.min()) )
    elif inverse:
      amin = ( umin/(1.0 + U.min()) - umax/(1.0 + U.max()) ) / (umax - umin)
      amax = umin / ( amin + 1.0/(1.0 + U.max()) )
    
    self.data[fn] = (amin + 1.0/(1.0 + U)) * amax

  def __identify_nans(self, data, fn):
    """
    private method to identify rows and columns of all nans from grids. This
    happens when the data from multiple GIS databases don't quite align on
    whatever the desired grid is.
    """
    good_x = ~all(isnan(data), axis=0) & self.good_x  # good cols
    good_y = ~all(isnan(data), axis=1) & self.good_y  # good rows

    if any(good_x != self.good_x):
      total_nan_x = sum(good_x == False)
      self.rem_nans = True
      s =  "Warning: %d row(s) of \"%s\" are entirely NaN." % (total_nan_x, fn)
      print_text(s, self.color)

    if any(good_y != self.good_y):
      total_nan_y = sum(good_y == False)
      self.rem_nans = True
      s = "Warning: %d col(s) of \"%s\" are entirely NaN." % (total_nan_y, fn)
      print_text(s, self.color)

    self.good_x = good_x
    self.good_y = good_y

  def __remove_nans(self):
    """
    remove extra rows/cols from data where NaNs were identified and set the
    extents to those of the good x and y values.
    """
    s = "::: removing NaNs from %s :::" % self.name
    print_text(s, self.color)

    self.x     = self.x[self.good_x]
    self.y     = self.y[self.good_y]
    self.x_min = self.x.min()
    self.x_max = self.x.max()
    self.y_min = self.y.min()
    self.y_max = self.y.max()
    self.nx    = len(self.x)
    self.ny    = len(self.y)

    for i in self.data.keys():
      self.data[i] = self.data[i][self.good_y, :          ]
      self.data[i] = self.data[i][:,           self.good_x]

  def set_data_min(self, fn, boundary, val):
    """
    Set all values of the array associated with this instance's ``self.data`` 
    dictionary with key ``fn`` below ``boundary`` to value ``val``.

    :param fn: key of data array
    :param boundary: boundary below which to assign
    :param val:      boundary to assign
    :type fn: string
    :type boundary: float
    :type val: float
    """
    s    = "::: setting any value of %s's %s field below %.3e to %.3e :::" \
           % (self.name, fn, boundary, val)
    print_text(s, self.color)
    
    d                = self.data[fn]
    d[d <= boundary] = val
    self.data[fn]    = d

  def set_data_max(self, fn, boundary, val):
    """
    Set all values of the array associated with this instance's ``self.data`` 
    dictionary with key ``fn`` above ``boundary`` to value ``val``.

    :param fn: key of data array
    :param boundary: boundary below which to assign
    :param val:      boundary to assign
    :type fn: string
    :type boundary: float
    :type val: float
    """
    s    = "::: setting any value of %s's %s field above %.3e to %.3e :::" \
           % (self.name, fn, boundary, val)
    print_text(s, self.color)
    
    d                = self.data[fn]
    d[d >= boundary] = val
    self.data[fn]    = d

  def set_data_val(self, fn, old_val, new_val):
    """
    Set all values of the array associated with this instance's ``self.data`` 
    dictionary with key ``fn`` with value ``old_val`` to ``new_val``.

    :param fn: key of data array
    :param old_val: the value to change
    :param new_val: the value to replace
    :type fn: string
    :type old_val: float
    :type new_val: float
    """
    s    = "::: setting all values of %s's %s field equal to %.3e to %.3e :::" \
           % (self.name, fn, old_val, new_val)
    print_text(s, self.color)
    
    d                = self.data[fn]
    d[d == old_val]  = new_val
    self.data[fn]    = d

  def get_expression(self, fn, order=1, near=False):
    """
    Creates a spline-interpolation expression for data with key ``fn`` with 
    order of approximation in :math:`x` and :math:`y` directions ``order``.
    if ``near == True``, use nearest-neighbor interpolation.

    :param fn: key of data to form expression of
    :param order: order of the interpolation
    :param near:  use nearest-neighbor interpolation
    :type fn: string
    :type order: int
    :type near: bool
    """
    if near:  t = 'nearest-neighbor'
    else:     t = 'O(%i) spline' % order
    s = "::: getting '%s' %s expression from '%s' :::" % (fn, t, self.name)
    print_text(s, self.color)

    data = self.data[fn]

    if self.chg_proj:
      new_proj = self.new_p
      old_proj = self.proj

    if not near:
      spline = RectBivariateSpline(self.x, self.y, data.T, kx=order, ky=order)
    else:
      interp_x = interp1d(self.x, arange(len(self.x)), kind='nearest')
      interp_y = interp1d(self.y, arange(len(self.y)), kind='nearest')

    xs       = self.x
    ys       = self.y
    chg_proj = self.chg_proj

    class CslvrExpression(Expression):
      """
      Class that handles interpolation between altered projection coordinates.
      """
      def eval(self, values, x):
        if chg_proj:
          xn, yn = transform(new_proj, old_proj, x[0], x[1])
        else:
          xn, yn = x[0], x[1]
        if not near:
          values[0] = spline(xn, yn)
        else:
          idx       = int(interp_x(xn))
          idy       = int(interp_y(yn))
          values[0] = data[idy, idx]

    return CslvrExpression(element = self.element)


def print_min_max(u, title, color='97'):
  """
  Print the minimum and maximum values of ``u``, a Vector, Function, or array.

  :param u: the variable to print the min and max of
  :param title: the name of the function to print
  :param color: the color of printed text
  :type u: :class:`~dolfin.GenericVector`, :class:`~numpy.ndarray`, :class:`~dolfin.Function`, int, float, :class:`~dolfin.Constant`
  :type title: string
  :type color: string
  """
  if isinstance(u, GenericVector):
    uMin = MPI.min(mpi_comm_world(), u.min())
    uMax = MPI.max(mpi_comm_world(), u.max())
    s    = title + ' <min, max> : <%.3e, %.3e>' % (uMin, uMax)
    print_text(s, color)
  elif isinstance(u, indexed.Indexed):
    dim = u.value_rank() + 1
    for i in range(u.value_rank()):
      uMin = u.vector().array()[i : u.vector().size() : dim].min()
      uMax = u.vector().array()[i : u.vector().size() : dim].max()
      s    = title + '_%i <min, max> : <%.3e, %.3e>' % (i, uMin, uMax)
      print_text(s, color)
  elif isinstance(u, ndarray):
    if u.dtype != float64:
      u = u.astype(float64)
    uMin = MPI.min(mpi_comm_world(), u.min())
    uMax = MPI.max(mpi_comm_world(), u.max())
    s    = title + ' <min, max> : <%.3e, %.3e>' % (uMin, uMax)
    print_text(s, color)
  elif isinstance(u, Function):# \
    #   or isinstance(u, dolfin.functions.function.Function):
    uMin = MPI.min(mpi_comm_world(), u.vector().min())
    uMax = MPI.max(mpi_comm_world(), u.vector().max())
    s    = title + ' <min, max> : <%.3e, %.3e>' % (uMin, uMax)
    print_text(s, color)
  elif isinstance(u, int) or isinstance(u, float):
    s    = title + ' : %.3e' % u
    print_text(s, color)
  elif isinstance(u, Constant):
    s    = title + ' : %.3e' % u(0)
    print_text(s, color)
  else:
    er = title + ": print_min_max function requires a Vector, Function" \
         + ", array, int or float, not %s." % type(u)
    print_text(er, 'red', 1)


def get_text(text, color='white', atrb=0, cls=None):
  """
  Returns text ``text`` from calling class ``cls`` for printing at a later time.

  :param text: the text to print
  :param color: the color of the text to print
  :param atrb: attributes to send use by ``colored`` package
  :param cls: the calling class
  :type text: string
  :type color: string
  :type atrb: int
  :type cls: object
  """
  if cls is not None:
    color = cls.color()
  if MPI.rank(mpi_comm_world())==0:
    if atrb != 0:
      text = ('%s%s' + text + '%s') % (fg(color), attr(atrb), attr(0))
    else:
      text = ('%s' + text + '%s') % (fg(color), attr(0))
    return text


def print_text(text, color='white', atrb=0, cls=None):
  """
  Print text ``text`` from calling class ``cls`` to the screen.

  :param text: the text to print
  :param color: the color of the text to print
  :param atrb: attributes to send use by ``colored`` package
  :param cls: the calling class
  :type text: string
  :type color: string
  :type atrb: int
  :type cls: object
  """
  if cls is not None:
    color = cls.color()
  if MPI.rank(mpi_comm_world())==0:
    if atrb != 0:
      text = ('%s%s' + text + '%s') % (fg(color), attr(atrb), attr(0))
    else:
      text = ('%s' + text + '%s') % (fg(color), attr(0))
    print text



