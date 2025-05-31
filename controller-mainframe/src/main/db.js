import Datastore from 'nedb';

const charactersDB = new Datastore({
    filename: 'characters.db',
    autoload: true
});

export {charactersDB};
