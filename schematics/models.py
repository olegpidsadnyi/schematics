import inspect
import copy


from schematics.base import (TypeException, ModelException, json)
from schematics.types import (DictFieldNotFound, schematic_types, BaseType,
                              UUIDType)


__all__ = ['ModelMetaclass', 'TopLevelModelMetaclass', 'BaseModel',
           'Model', 'TypeException']


###
### Model Configuration
###

class ModelOptions(object):
    """This class is a container for all metaclass configuration options. It's
    primary purpose is to create an instance of a model's options for every
    instance of a model.

    It also creates errors in cases where unknown options parameters are found.
    """
    def __init__(self, klass, db_namespace=None, # permissions=None,
                 private_fields=None, public_fields=None):
        self.klass = klass
        self.db_namespace = db_namespace
        #self.permissions = permissions
        self.private_fields = private_fields
        self.public_fields = public_fields


def _parse_options_config(klass, attrs, options_class):
    """Parses the Options object on the class and translates it into an Option
    instance.
    """
    valid_attrs = dict()
    if 'Options' in attrs:
        options = attrs['Options']
        for attr_name, attr_value in inspect.getmembers(options):
            if not attr_name.startswith('_'):
                valid_attrs[attr_name] = attr_value
    oc = options_class(klass, **valid_attrs)
    return oc


def _gen_options(klass, attrs):
    """Processes the attributes and class parameters to generate the correct
    options schematic.

    Defaults to `ModelOptions` but it's ideal to define `__optionsclass_`
    on the Model's metaclass.
    """
    ### Parse Options
    options_class = ModelOptions
    if hasattr(klass, '__optionsclass__'):
        options_class = klass.__optionsclass__
    options = _parse_options_config(klass, attrs, options_class)
    return options


def _extract_fields(bases, attrs):
    ### Collect all fields in here
    model_fields = {}

    ### Aggregate fields found in base classes first
    for base in bases:
        ### Configure `_fields` list
        if hasattr(base, '_fields'):
            model_fields.update(base._fields)

    ### Collect field info from attrs
    for attr_name, attr_value in attrs.items():
        has_class = hasattr(attr_value, "__class__")
        if has_class and issubclass(attr_value.__class__, BaseType):
            ### attr_name = field name
            ### attr_value = field instance
            attr_value.field_name = attr_name  # fields know their name
            model_fields[attr_name] = attr_value
            
    return model_fields


###
### Metaclass Design
###

class ModelMetaclass(type):
    def __new__(cls, name, bases, attrs):
        """Processes a configuration of a Model type into a class.
        """
        ### Gen a class instance
        klass = type.__new__(cls, name, bases, attrs)

        ### Parse metaclass config into options schematic
        options = _gen_options(klass, attrs)
        if hasattr(klass, 'Options'):
            delattr(klass, 'Options')

        ### Extract fields and attach klass as owner
        fields =  _extract_fields(bases, attrs)
        for field in fields.values():
            field.owner_model = klass

        ### Attach collected data to klass
        setattr(klass, '_options', options)
        setattr(klass, '_fields', fields)
        setattr(klass, '_model_name', name)

        ### Fin.
        return klass

    def __str__(self):
        if hasattr(self, '__unicode__'):
            return unicode(self).encode('utf-8')
        return '%s object' % self.__class__.__name__


###
### Model schematics
###

class BaseModel(object):

    def __init__(self, **values):
        self._data = {}
        minimized_field_map = {}

        # Assign default values to instance
        for attr_name, attr_value in self._fields.items():
            # Use default value if present
            value = getattr(self, attr_name, None)
            setattr(self, attr_name, value)
            if attr_value.minimized_field_name:
                field_name = attr_value.minimized_field_name
                minimized_field_map[field_name] = attr_value.uniq_field

        # Assign initial values to instance
        for attr_name, attr_value in values.items():
            try:
                setattr(self, attr_name, attr_value)
                if attr_name in minimized_field_map:
                    setattr(self, minimized_field_map[attr_name], attr_value)
            # Put a diaper on the keys that don't belong and send 'em home
            except AttributeError:
                pass


    ###
    ### Validation functions
    ###

    def validate(self, validate_all=False):
        """Ensure that all fields' values are valid and that required fields
        are present.

        Throws a ModelException if Model is invalid
        """
        # Get a list of tuples of field names and their current values
        fields = [(field, getattr(self, name))
                  for name, field in self._fields.items()]

        # Ensure that each field is matched to a valid value
        errs = []
        for field, value in fields:
            err = None
            # treat empty strings as nonexistent
            if value is not None and value != '':
                try:
                    field._validate(value)
                except TypeException, e:
                    err = e
                except (ValueError, AttributeError, AssertionError):
                    err = TypeException('Invalid value', field.field_name,
                                        value)
            elif field.required:
                err = TypeException('Required field missing',
                                    field.field_name,
                                    value)
            # If validate_all, save errors to a list
            # Otherwise, throw the first error
            if err:
                errs.append(err)
            if err and not validate_all:
                # NB: raising a ModelException in this case would be more
                # consistent, but existing code might expect TypeException
                raise err

        if errs:
            raise ModelException(self._model_name, errs)
        return True


    ###
    ### Implement the dictionary interface
    ###

    def __iter__(self):
        return iter(self._fields)

    def __getitem__(self, name):
        """Dictionary-style field access, return a field's value if present.
        """
        try:
            if name in self._fields:
                return getattr(self, name)
        except AttributeError:
            pass
        raise KeyError(name)

    def __setitem__(self, name, value):
        """Dictionary-style field access, set a field's value.
        """
        # Ensure that the field exists before settings its value
        if name not in self._fields:
            raise KeyError(name)
        return setattr(self, name, value)

    def __contains__(self, name):
        try:
            val = getattr(self, name)
            return val is not None
        except AttributeError:
            return False

    def __len__(self):
        return len(self._data)

    def __eq__(self, other):
        if isinstance(other, self.__class__):
            keys = self._fields
            if not hasattr(other, 'id'):
                keys.pop("id", None)
            for key in keys:
                if self[key] != other[key]:
                    return False
            return True
        return False

    ###
    ### Representation Descriptors
    ###
    
    def __repr__(self):
        try:
            u = unicode(self)
        except (UnicodeEncodeError, UnicodeDecodeError):
            u = '[Bad Unicode data]'
        return u"<%s: %s>" % (self.__class__.__name__, u)

    def __str__(self):
        if hasattr(self, '__unicode__'):
            return unicode(self).encode('utf-8')
        return '%s object' % self.__class__.__name__
    
###
### Model Manipulation Functions
###

def swap_field(klass, new_field, fields):
    """This function takes an existing class definition `klass` and create a
    new version of the schematic with the fields in `fields` converted to
    `field` instances.

    Effectively doing this:

        class.field_name = id_field()  # like ObjectIdType, perhaps

    Returns the class for compatibility, making it compatible with a decorator.
    """
    ### The metaclass attributes will fake not having inheritance
    cn = klass._model_name
    sc = klass._superclasses
    klass_name = klass.__name__
    new_klass = type(klass_name, (klass,), {})

    ### Generate the id_fields for each field we're updating. Mark the actual
    ### id_field as the uniq_field named '_id'
    fields_dict = dict()
    for f in fields:
        if f is 'id':
            new_klass._fields[f] = new_field(uniq_field='_id')
        else:
            new_klass._fields[f] = new_field()

    new_klass.id = new_klass._fields['id']
    return new_klass


def diff_id_field(id_field, field_list, *arg):
    """This function is a decorator that takes an id field, like ObjectIdType,
    and replaces the fields in `field_list` to use `id_field` instead.

    Wrap a class definition and it will apply the field swap in an simply and
    expressive way.

    The function can be used as a decorator OR the adjusted class can be passed
    as an optional third argument.
    """
    if len(arg) == 1:
        return swap_field(arg[0], id_field, field_list)

    def wrap(klass):
        klass = swap_field(klass, id_field, field_list)
        return klass
    return wrap


###
### Validation functions
###

def _gen_handle_exception(validate_all, exception_list):
    """Generates a function for either raising exceptions or collecting
    them in a list.
    """
    if validate_all:
        def handle_exception(e):
            exception_list.append(e)
    else:
        def handle_exception(e):
            raise e

    return handle_exception


def _gen_handle_class_field(delete_rogues, field_list):
    """Generates a function that either accumulates observed fields or
    makes no attempt to collect them.

    The case where nothing accumulates is to prevent growing data
    schematics unnecessarily.
    """
    if delete_rogues:
        def handle_class_field(cf):
            field_list.append(cf)
    else:
        def handle_class_field(cf):
            pass

    return handle_class_field

def _validate_helper(cls, field_inspector, values, validate_all=False,
                     delete_rogues=True):
    """This is a convenience function that loops over the given values
    and attempts to validate them against the class definition. It only
    validates the data in values and does not guarantee a complete model
    is present.

    'not present' is defined as not having a value OR having '' (or u'')
    as a value.
    """
    if not hasattr(cls, '_fields'):
        raise ValueError('cls is not a Model instance')

    # Create function for handling exceptions
    exceptions = list()
    handle_exception = _gen_handle_exception(validate_all, exceptions)

    # Create function for handling a flock of frakkin palins (rogue fields)
    data_fields = set(values.keys())
    class_fields = list()
    handle_class_field = _gen_handle_class_field(delete_rogues,
                                                 class_fields)

    # Loop across fields present in model
    for k, v in cls._fields.items():

        # handle common id name
        if k is 'id':
            k = '_id'

        handle_class_field(k)

        if field_inspector(k, v):
            datum = values[k]
            # if datum is None, skip
            if datum is None:
                continue
            # treat empty strings as empty values and skip
            if isinstance(datum, (str, unicode)) and \
                   len(datum.strip()) == 0:
                continue
            try:
                v.validate(datum)
            except TypeException, e:
                handle_exception(e)

    # Remove rogue fields
    if len(class_fields) > 0:  # if accumulation is not disabled
        palins = data_fields - set(class_fields)
        for rogue_field in palins:
            del values[rogue_field]

    # Reaches here only if exceptions are aggregated or validation passed
    if validate_all:
        return exceptions
    else:
        return True

def validate_class_fields(cls, values, validate_all=False):
    """This is a convenience function that loops over _fields in
    cls to validate them. If the field is not required AND not present,
    it is skipped.
    """
    fun = lambda k, v: v.required or k in values
    return _validate_helper(cls, fun, values, validate_all=validate_all)

def validate_class_partial(cls, values, validate_all=False):
    """This is a convenience function that loops over _fields in
    cls to validate them. This function is a partial validatation
    only, meaning the values given and does not check if the model
    is complete.
    """
    fun = lambda k, v: k in values
    return _validate_helper(cls, fun, values, validate_all=validate_all)


class Model(BaseModel):
    """Model YEAH
    """
    __metaclass__ = ModelMetaclass
    __optionsclass__ = ModelOptions