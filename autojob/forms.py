"""
WTForms for authentication.

Using server-rendered forms (with Flask-WTF) gives us CSRF protection for free
on every POST — the hidden token is validated automatically. Validation lives
here so the same rules apply whether the request comes from the browser form or
a script.
"""

from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import BooleanField, PasswordField, StringField
from wtforms.validators import (
    Email,
    EqualTo,
    InputRequired,
    Length,
    Regexp,
)

_PASSWORD_POLICY = Length(min=8, max=128, message="Use at least 8 characters.")


class RegisterForm(FlaskForm):
    name = StringField("Full name", validators=[InputRequired(), Length(max=120)])
    email = StringField(
        "Work email",
        validators=[InputRequired(), Email(message="Enter a valid email."), Length(max=255)],
    )
    password = PasswordField(
        "Password",
        validators=[
            InputRequired(),
            _PASSWORD_POLICY,
            Regexp(r".*[A-Za-z].*", message="Include at least one letter."),
            Regexp(r".*\d.*", message="Include at least one number."),
        ],
    )
    confirm = PasswordField(
        "Confirm password",
        validators=[InputRequired(), EqualTo("password", message="Passwords must match.")],
    )
    accept_terms = BooleanField(
        "I agree to the Terms and Privacy Policy",
        validators=[InputRequired(message="You must accept the terms to continue.")],
    )


class LoginForm(FlaskForm):
    email = StringField("Email", validators=[InputRequired(), Email(), Length(max=255)])
    password = PasswordField("Password", validators=[InputRequired()])
    remember = BooleanField("Keep me signed in")
