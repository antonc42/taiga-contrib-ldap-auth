# Copyright (C) 2014 Andrey Antukh <niwi@niwi.be>
# Copyright (C) 2014 Jesús Espino <jespinog@gmail.com>
# Copyright (C) 2014 David Barragán <bameda@dbarragan.com>
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

from django.db import transaction as tx
from django.conf import settings
from django.apps import apps

from taiga.base.connectors.exceptions import ConnectorBaseException, BaseException
from taiga.base.utils.slug import slugify_uniquely
from taiga.auth.services import make_auth_response_data
from taiga.auth.signals import user_registered as user_registered_signal
from taiga.auth.services import get_auth_plugins

from . import connector


FALLBACK = getattr(settings, "LDAP_FALLBACK", "")


@tx.atomic
def ldap_register(username: str, email: str, full_name: str):
    """
    Register a new user from LDAP.

    Can raise `exc.IntegrityError` exceptions in
    case of conflict found.

    :returns: User
    """
    user_model = apps.get_model("users", "User")

    try:
        # LDAP user association exists? (case sensitive)
        user = user_model.objects.get(username__exact = username)
    except user_model.DoesNotExist:
        try:
            # LDAP user association exists? (case insensitive)
            user = user_model.objects.get(username__iexact = username)
            # LDAP email exists
            user_model.objects.get(email__iexact = email)
        # if no case sensitive match for user or case insensitive match for user and email, create a new user
        except user_model.DoesNotExist:
            # Create a new user
            username_unique = slugify_uniquely(username, user_model, slugfield = "username")
            user = user_model.objects.create(username = username_unique,
                                             email = email,
                                             full_name = full_name)
            user_registered_signal.send(sender = user.__class__, user = user)
            

    # update DB entry if LDAP field values differ
    if user.email != email or user.full_name != full_name:
        user_model.objects.filter(pk = user.pk).update(email = email, full_name = full_name)
        user.refresh_from_db()

    return user


def ldap_login_func(request):
    # although the form field is called 'username', it can be an e-mail
    # (or any other attribute)
    login_input = request.DATA.get('username', None)
    password_input = request.DATA.get('password', None)

    try:
        # TODO: make sure these fields are sanitized before passing to LDAP server!
        username, email, full_name = connector.login(login = login_input, password = password_input)
    except connector.LDAPUserLoginError as ldap_error:
        # If no fallback authentication is specified, raise the original LDAP error
        if not FALLBACK:
            raise

        # Try normal authentication
        try:
            return get_auth_plugins()["normal"]["login_func"](request)
        except BaseException as normal_error:
            # Merge error messages of 'normal' and 'ldap' auth.
            raise ConnectorBaseException({
                "error_message": {
                    "ldap": ldap_error.detail["error_message"],
                    "normal": normal_error.detail
                }
            })
    else:
        user = ldap_register(username = username, email = email, full_name = full_name)
        data = make_auth_response_data(user)
        return data
