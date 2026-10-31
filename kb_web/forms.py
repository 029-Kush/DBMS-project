"""Forms: server-side validation and CSRF tokens (Flask-WTF)."""
from flask_wtf import FlaskForm
from wtforms import IntegerField, PasswordField, SelectField, StringField, TextAreaField
from wtforms.validators import DataRequired, EqualTo, Length, NumberRange, Optional, Regexp

ENTITY_TYPES = ["tool", "concept", "manpage", "syscall", "library", "other"]


class RegisterForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired(), Regexp(r"^[a-z0-9_]{3,32}$",
                           message="3-32 characters: lowercase letters, digits, underscore")])
    email = StringField("Email", validators=[DataRequired(), Length(max=254),
                        Regexp(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", message="Enter a valid email address")])
    password = PasswordField("Password", validators=[DataRequired(), Length(min=10, max=72,
                             message="10-72 characters (bcrypt reads at most 72 bytes)")])
    confirm = PasswordField("Confirm password", validators=[DataRequired(), EqualTo("password", message="Passwords differ")])


class LoginForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired(), Length(max=32)])
    password = PasswordField("Password", validators=[DataRequired(), Length(max=72)])


class EntityForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired(), Length(max=200)])
    type = SelectField("Type", choices=[(t, t) for t in ENTITY_TYPES])
    aliases = StringField("Aliases (comma separated)", validators=[Optional(), Length(max=500)])
    version = StringField("Version", validators=[Optional(), Length(max=100)])
    summary = TextAreaField("Summary", validators=[Optional(), Length(max=2000)])


class PassageForm(FlaskForm):
    section = StringField("Section", validators=[DataRequired(), Length(max=100)])
    text = TextAreaField("Text", validators=[DataRequired(), Length(min=30, max=5000)])


class NoteForm(FlaskForm):
    note = StringField("Note", validators=[Optional(), Length(max=500)])


class RoleForm(FlaskForm):
    role = SelectField("Role", choices=[("viewer", "viewer"), ("editor", "editor"), ("admin", "admin")])


class MergeForm(FlaskForm):
    src = IntegerField("Merge entity id", validators=[DataRequired(), NumberRange(min=1)])
    dst = IntegerField("into entity id", validators=[DataRequired(), NumberRange(min=1)])
