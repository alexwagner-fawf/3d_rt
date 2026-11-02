-- Gesamtstruktur TMS/Referenzwetter, Stand 20261102_0926 – mit psql ausführen
CREATE SCHEMA IF NOT EXISTS daten;
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE SCHEMA IF NOT EXISTS plots;
CREATE SCHEMA IF NOT EXISTS lut;
--
-- PostgreSQL database dump
--

-- Dumped from database version 12.22 (Ubuntu 12.22-0ubuntu0.20.04.4)
-- Dumped by pg_dump version 17.5 (Ubuntu 17.5-1.pgdg20.04+1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: behandlungseinheiten; Type: TABLE; Schema: lut; Owner: fsadmin
--

CREATE TABLE lut.behandlungseinheiten (
    behandlungseinheit_id smallint NOT NULL,
    be_name_short_eng character varying(3) NOT NULL,
    be_name_short_de character varying(3) NOT NULL,
    be_name_safe_eng character varying(50) NOT NULL,
    be_name_safe_de character varying(50) NOT NULL,
    be_name_clean_eng character varying(50) NOT NULL,
    be_name_clean_de character varying(50) NOT NULL
);


ALTER TABLE lut.behandlungseinheiten OWNER TO fsadmin;

--
-- Name: versuchsflaechen; Type: TABLE; Schema: lut; Owner: fsadmin
--

CREATE TABLE lut.versuchsflaechen (
    versuchsflaeche_id smallint NOT NULL,
    versuchsflaeche_name_safe character varying(50) NOT NULL,
    versuchsflaeche_name_clean character varying(50) NOT NULL,
    versuchsflaeche_name_short character varying(3) NOT NULL
);


ALTER TABLE lut.versuchsflaechen OWNER TO fsadmin;

--
-- Name: be_flaechen; Type: TABLE; Schema: plots; Owner: geoadmin
--

CREATE TABLE plots.be_flaechen (
    fl_id integer NOT NULL,
    flaeche_ha double precision,
    versuchsflaeche_id integer NOT NULL,
    behandlungseinheit_id integer NOT NULL,
    kommentar text,
    geom public.geometry
);


ALTER TABLE plots.be_flaechen OWNER TO geoadmin;

--
-- Name: TABLE be_flaechen; Type: COMMENT; Schema: plots; Owner: geoadmin
--

COMMENT ON TABLE plots.be_flaechen IS 'Flächen inkl. ehemaliger';


--
-- Name: be_plots; Type: TABLE; Schema: plots; Owner: geoadmin
--

CREATE TABLE plots.be_plots (
    pkr_id integer NOT NULL,
    pkr_name text,
    fl_id integer,
    bearbeiter text,
    kommentar text,
    geometry public.geometry
);


ALTER TABLE plots.be_plots OWNER TO geoadmin;

--
-- Name: TABLE be_plots; Type: COMMENT; Schema: plots; Owner: geoadmin
--

COMMENT ON TABLE plots.be_plots IS 'Plotmittelpunkte';


--
-- Name: behandlungseinheiten_behandlungseinheit_id_seq; Type: SEQUENCE; Schema: lut; Owner: fsadmin
--

CREATE SEQUENCE lut.behandlungseinheiten_behandlungseinheit_id_seq
    AS smallint
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE lut.behandlungseinheiten_behandlungseinheit_id_seq OWNER TO fsadmin;

--
-- Name: behandlungseinheiten_behandlungseinheit_id_seq; Type: SEQUENCE OWNED BY; Schema: lut; Owner: fsadmin
--

ALTER SEQUENCE lut.behandlungseinheiten_behandlungseinheit_id_seq OWNED BY lut.behandlungseinheiten.behandlungseinheit_id;


--
-- Name: versuchsflaechen_versuchsflaeche_id_seq; Type: SEQUENCE; Schema: lut; Owner: fsadmin
--

CREATE SEQUENCE lut.versuchsflaechen_versuchsflaeche_id_seq
    AS smallint
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE lut.versuchsflaechen_versuchsflaeche_id_seq OWNER TO fsadmin;

--
-- Name: versuchsflaechen_versuchsflaeche_id_seq; Type: SEQUENCE OWNED BY; Schema: lut; Owner: fsadmin
--

ALTER SEQUENCE lut.versuchsflaechen_versuchsflaeche_id_seq OWNED BY lut.versuchsflaechen.versuchsflaeche_id;


--
-- Name: be_verjuengungskreise_pt; Type: TABLE; Schema: plots; Owner: geoadmin
--

CREATE TABLE plots.be_verjuengungskreise_pt (
    verjkr_id character varying(9) NOT NULL,
    pkr_id integer NOT NULL,
    aktiv boolean,
    bemerkungen text,
    geometry public.geometry
);


ALTER TABLE plots.be_verjuengungskreise_pt OWNER TO geoadmin;

--
-- Name: TABLE be_verjuengungskreise_pt; Type: COMMENT; Schema: plots; Owner: geoadmin
--

COMMENT ON TABLE plots.be_verjuengungskreise_pt IS 'Mittelpunkte der Verjüngungskreise';


--
-- Name: verjuengungskreise_full_view; Type: VIEW; Schema: plots; Owner: geoadmin
--

CREATE VIEW plots.verjuengungskreise_full_view AS
 SELECT verjkr.verjkr_id,
    pkr.pkr_id,
    fl.behandlungseinheit_id,
    be.be_name_short_eng,
    be.be_name_short_de,
    be.be_name_safe_eng,
    be.be_name_safe_de,
    be.be_name_clean_eng,
    be.be_name_clean_de,
    vfl.versuchsflaeche_id,
    vfl.versuchsflaeche_name_safe,
    vfl.versuchsflaeche_name_clean,
    vfl.versuchsflaeche_name_short,
    verjkr.bemerkungen,
    verjkr.geometry
   FROM ((((plots.be_verjuengungskreise_pt verjkr
     LEFT JOIN plots.be_plots pkr ON ((pkr.pkr_id = verjkr.pkr_id)))
     LEFT JOIN plots.be_flaechen fl ON ((fl.fl_id = pkr.fl_id)))
     LEFT JOIN lut.behandlungseinheiten be ON ((be.behandlungseinheit_id = fl.behandlungseinheit_id)))
     LEFT JOIN lut.versuchsflaechen vfl ON ((vfl.versuchsflaeche_id = fl.versuchsflaeche_id)))
  WHERE (verjkr.aktiv AND (verjkr.pkr_id < 1001));


ALTER VIEW plots.verjuengungskreise_full_view OWNER TO geoadmin;

--
-- Name: behandlungseinheiten behandlungseinheit_id; Type: DEFAULT; Schema: lut; Owner: fsadmin
--

ALTER TABLE ONLY lut.behandlungseinheiten ALTER COLUMN behandlungseinheit_id SET DEFAULT nextval('lut.behandlungseinheiten_behandlungseinheit_id_seq'::regclass);


--
-- Name: versuchsflaechen versuchsflaeche_id; Type: DEFAULT; Schema: lut; Owner: fsadmin
--

ALTER TABLE ONLY lut.versuchsflaechen ALTER COLUMN versuchsflaeche_id SET DEFAULT nextval('lut.versuchsflaechen_versuchsflaeche_id_seq'::regclass);


--
-- Name: behandlungseinheiten behandlungseinheiten_pkey; Type: CONSTRAINT; Schema: lut; Owner: fsadmin
--

ALTER TABLE ONLY lut.behandlungseinheiten
    ADD CONSTRAINT behandlungseinheiten_pkey PRIMARY KEY (behandlungseinheit_id);


--
-- Name: behandlungseinheiten unique_be_name_safe; Type: CONSTRAINT; Schema: lut; Owner: fsadmin
--

ALTER TABLE ONLY lut.behandlungseinheiten
    ADD CONSTRAINT unique_be_name_safe UNIQUE (be_name_safe_de);


--
-- Name: versuchsflaechen versuchsflaechen_pkey; Type: CONSTRAINT; Schema: lut; Owner: fsadmin
--

ALTER TABLE ONLY lut.versuchsflaechen
    ADD CONSTRAINT versuchsflaechen_pkey PRIMARY KEY (versuchsflaeche_id);


--
-- Name: versuchsflaechen vfl_unique_name_safe; Type: CONSTRAINT; Schema: lut; Owner: fsadmin
--

ALTER TABLE ONLY lut.versuchsflaechen
    ADD CONSTRAINT vfl_unique_name_safe UNIQUE (versuchsflaeche_name_safe);


--
-- Name: be_flaechen be_flaechen_neu_pkey; Type: CONSTRAINT; Schema: plots; Owner: geoadmin
--

ALTER TABLE ONLY plots.be_flaechen
    ADD CONSTRAINT be_flaechen_neu_pkey PRIMARY KEY (fl_id);


--
-- Name: be_verjuengungskreise_pt be_verjuengungskreise_pt_neu_pkey; Type: CONSTRAINT; Schema: plots; Owner: geoadmin
--

ALTER TABLE ONLY plots.be_verjuengungskreise_pt
    ADD CONSTRAINT be_verjuengungskreise_pt_neu_pkey PRIMARY KEY (verjkr_id);


--
-- Name: be_plots unique_pkr_id; Type: CONSTRAINT; Schema: plots; Owner: geoadmin
--

ALTER TABLE ONLY plots.be_plots
    ADD CONSTRAINT unique_pkr_id PRIMARY KEY (pkr_id);


--
-- Name: be_flaechen fk_flaechen_be_id; Type: FK CONSTRAINT; Schema: plots; Owner: geoadmin
--

ALTER TABLE ONLY plots.be_flaechen
    ADD CONSTRAINT fk_flaechen_be_id FOREIGN KEY (behandlungseinheit_id) REFERENCES lut.behandlungseinheiten(behandlungseinheit_id) NOT VALID;


--
-- Name: be_flaechen fk_flaechen_vfl_id; Type: FK CONSTRAINT; Schema: plots; Owner: geoadmin
--

ALTER TABLE ONLY plots.be_flaechen
    ADD CONSTRAINT fk_flaechen_vfl_id FOREIGN KEY (versuchsflaeche_id) REFERENCES lut.versuchsflaechen(versuchsflaeche_id) NOT VALID;


--
-- Name: be_verjuengungskreise_pt plots_be_verjuenugungskreise_pt_fk; Type: FK CONSTRAINT; Schema: plots; Owner: geoadmin
--

ALTER TABLE ONLY plots.be_verjuengungskreise_pt
    ADD CONSTRAINT plots_be_verjuenugungskreise_pt_fk FOREIGN KEY (pkr_id) REFERENCES plots.be_plots(pkr_id) NOT VALID;


--
-- Name: be_plots plots_plots_fk_be_id; Type: FK CONSTRAINT; Schema: plots; Owner: geoadmin
--

ALTER TABLE ONLY plots.be_plots
    ADD CONSTRAINT plots_plots_fk_be_id FOREIGN KEY (fl_id) REFERENCES plots.be_flaechen(fl_id) NOT VALID;


--
-- Name: TABLE be_flaechen; Type: ACL; Schema: plots; Owner: geoadmin
--

REVOKE SELECT,INSERT,REFERENCES,DELETE,TRIGGER,TRUNCATE,UPDATE ON TABLE plots.be_flaechen FROM geoadmin;
GRANT SELECT,INSERT,REFERENCES,DELETE,TRIGGER,TRUNCATE,UPDATE ON TABLE plots.be_flaechen TO geoadmin WITH GRANT OPTION;
GRANT SELECT,INSERT,REFERENCES,DELETE,TRIGGER,TRUNCATE,UPDATE ON TABLE plots.be_flaechen TO fsadmin WITH GRANT OPTION;
GRANT SELECT ON TABLE plots.be_flaechen TO geouser;


--
-- Name: TABLE be_plots; Type: ACL; Schema: plots; Owner: geoadmin
--

REVOKE SELECT,INSERT,REFERENCES,DELETE,TRIGGER,TRUNCATE,UPDATE ON TABLE plots.be_plots FROM geoadmin;
GRANT SELECT,INSERT,REFERENCES,DELETE,TRIGGER,TRUNCATE,UPDATE ON TABLE plots.be_plots TO geoadmin WITH GRANT OPTION;
GRANT SELECT,INSERT,REFERENCES,DELETE,TRIGGER,TRUNCATE,UPDATE ON TABLE plots.be_plots TO fsadmin WITH GRANT OPTION;
GRANT SELECT ON TABLE plots.be_plots TO geouser;


--
-- Name: TABLE be_verjuengungskreise_pt; Type: ACL; Schema: plots; Owner: geoadmin
--

REVOKE SELECT,INSERT,REFERENCES,DELETE,TRIGGER,TRUNCATE,UPDATE ON TABLE plots.be_verjuengungskreise_pt FROM geoadmin;
GRANT SELECT,INSERT,REFERENCES,DELETE,TRIGGER,TRUNCATE,UPDATE ON TABLE plots.be_verjuengungskreise_pt TO geoadmin WITH GRANT OPTION;
GRANT SELECT,INSERT,REFERENCES,DELETE,TRIGGER,TRUNCATE,UPDATE ON TABLE plots.be_verjuengungskreise_pt TO fsadmin WITH GRANT OPTION;
GRANT SELECT ON TABLE plots.be_verjuengungskreise_pt TO geouser;


--
-- Name: TABLE verjuengungskreise_full_view; Type: ACL; Schema: plots; Owner: geoadmin
--

REVOKE SELECT,INSERT,REFERENCES,DELETE,TRIGGER,TRUNCATE,UPDATE ON TABLE plots.verjuengungskreise_full_view FROM geoadmin;
GRANT SELECT,INSERT,REFERENCES,DELETE,TRIGGER,TRUNCATE,UPDATE ON TABLE plots.verjuengungskreise_full_view TO geoadmin WITH GRANT OPTION;
GRANT SELECT,INSERT,REFERENCES,DELETE,TRIGGER,TRUNCATE,UPDATE ON TABLE plots.verjuengungskreise_full_view TO fsadmin WITH GRANT OPTION;
GRANT SELECT ON TABLE plots.verjuengungskreise_full_view TO geouser;


--
-- PostgreSQL database dump complete
--

CREATE OR REPLACE FUNCTION daten.set_tms_deployment_id()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
    matched_deployment_id bigint;
    match_count integer;
BEGIN
    SELECT COUNT(*), MIN(p.deployment_id)
    INTO match_count, matched_deployment_id
    FROM daten.tms_deployments AS p
    WHERE p.serial_number = NEW.serial_number
      AND NEW.datetime >= (p.start_date::timestamp        AT TIME ZONE 'Europe/Berlin')
      AND NEW.datetime <  ((p.collect_date + 1)::timestamp AT TIME ZONE 'Europe/Berlin');
    IF match_count = 0 THEN
        RAISE EXCEPTION
            'No deployment found for logger % at timestamp %',
            NEW.serial_number, NEW.datetime;

    ELSIF match_count > 1 THEN
        RAISE EXCEPTION
            'Overlapping deployments for logger % at timestamp %',
            NEW.serial_number, NEW.datetime;
    END IF;

    NEW.deployment_id := matched_deployment_id;

    RETURN NEW;
END;
$function$
;
ALTER FUNCTION daten.set_tms_deployment_id() OWNER TO geoadmin;

--
-- PostgreSQL database dump
--

-- Dumped from database version 12.22 (Ubuntu 12.22-0ubuntu0.20.04.4)
-- Dumped by pg_dump version 17.5 (Ubuntu 17.5-1.pgdg20.04+1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: tms_data; Type: TABLE; Schema: daten; Owner: geoadmin
--

CREATE TABLE daten.tms_data (
    deployment_id bigint NOT NULL,
    serial_number text NOT NULL,
    height_id integer NOT NULL,
    sensor_name_id integer NOT NULL,
    datetime timestamp with time zone NOT NULL,
    value double precision
);


ALTER TABLE daten.tms_data OWNER TO geoadmin;

--
-- Name: tms_deployments; Type: TABLE; Schema: daten; Owner: geoadmin
--

CREATE TABLE daten.tms_deployments (
    deployment_id bigint NOT NULL,
    serial_number text NOT NULL,
    locality_id text,
    locality_id_quality text,
    start_date date NOT NULL,
    collect_date date NOT NULL,
    true_end_date date,
    logger_intact boolean,
    logger_error text,
    notes text,
    CONSTRAINT chk_tms_deployments_dates CHECK ((collect_date >= start_date))
);


ALTER TABLE daten.tms_deployments OWNER TO geoadmin;

--
-- Name: COLUMN tms_deployments.true_end_date; Type: COMMENT; Schema: daten; Owner: geoadmin
--

COMMENT ON COLUMN daten.tms_deployments.true_end_date IS 'Letzter vollständig gültiger Tag (Europe/Berlin); Standard beim Import = collect_date';


--
-- Name: tms_sensor_end; Type: TABLE; Schema: daten; Owner: geoadmin
--

CREATE TABLE daten.tms_sensor_end (
    deployment_id bigint NOT NULL,
    sensor_name_id integer NOT NULL,
    true_end_date date NOT NULL,
    reason text,
    edited_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE daten.tms_sensor_end OWNER TO geoadmin;

--
-- Name: TABLE tms_sensor_end; Type: COMMENT; Schema: daten; Owner: geoadmin
--

COMMENT ON TABLE daten.tms_sensor_end IS 'Enddatum einzelner Sensoren; wirksam ist LEAST(Logger-Enddatum, Sensor-Enddatum)';


--
-- Name: COLUMN tms_sensor_end.true_end_date; Type: COMMENT; Schema: daten; Owner: geoadmin
--

COMMENT ON COLUMN daten.tms_sensor_end.true_end_date IS 'Letzter vollständig gültiger Tag (Europe/Berlin)';


--
-- Name: tms_data_valid; Type: VIEW; Schema: daten; Owner: geoadmin
--

CREATE VIEW daten.tms_data_valid AS
 SELECT t.deployment_id,
    t.serial_number,
    t.height_id,
    t.sensor_name_id,
    t.datetime,
    t.value
   FROM ((daten.tms_data t
     JOIN daten.tms_deployments d ON ((d.deployment_id = t.deployment_id)))
     LEFT JOIN daten.tms_sensor_end s ON (((s.deployment_id = t.deployment_id) AND (s.sensor_name_id = t.sensor_name_id))))
  WHERE (t.datetime < COALESCE(timezone('Europe/Berlin'::text, ((LEAST(d.true_end_date, s.true_end_date) + 1))::timestamp without time zone), 'infinity'::timestamp with time zone));


ALTER VIEW daten.tms_data_valid OWNER TO geoadmin;

--
-- Name: tms_deployments_deployment_id_seq; Type: SEQUENCE; Schema: daten; Owner: geoadmin
--

ALTER TABLE daten.tms_deployments ALTER COLUMN deployment_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME daten.tms_deployments_deployment_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: tms_lut_height; Type: TABLE; Schema: daten; Owner: geoadmin
--

CREATE TABLE daten.tms_lut_height (
    height_id integer NOT NULL,
    height text
);


ALTER TABLE daten.tms_lut_height OWNER TO geoadmin;

--
-- Name: tms_lut_sensor_name; Type: TABLE; Schema: daten; Owner: geoadmin
--

CREATE TABLE daten.tms_lut_sensor_name (
    sensor_name_id integer NOT NULL,
    sensor_name text
);


ALTER TABLE daten.tms_lut_sensor_name OWNER TO geoadmin;

--
-- Name: tms_deployments pk_tms_deployments; Type: CONSTRAINT; Schema: daten; Owner: geoadmin
--

ALTER TABLE ONLY daten.tms_deployments
    ADD CONSTRAINT pk_tms_deployments PRIMARY KEY (deployment_id);


--
-- Name: tms_sensor_end pk_tms_sensor_end; Type: CONSTRAINT; Schema: daten; Owner: geoadmin
--

ALTER TABLE ONLY daten.tms_sensor_end
    ADD CONSTRAINT pk_tms_sensor_end PRIMARY KEY (deployment_id, sensor_name_id);


--
-- Name: tms_lut_height tms_lut_height_pkey; Type: CONSTRAINT; Schema: daten; Owner: geoadmin
--

ALTER TABLE ONLY daten.tms_lut_height
    ADD CONSTRAINT tms_lut_height_pkey PRIMARY KEY (height_id);


--
-- Name: tms_lut_sensor_name tms_lut_sensor_name_pkey; Type: CONSTRAINT; Schema: daten; Owner: geoadmin
--

ALTER TABLE ONLY daten.tms_lut_sensor_name
    ADD CONSTRAINT tms_lut_sensor_name_pkey PRIMARY KEY (sensor_name_id);


--
-- Name: tms_data uq_tms_data_observation; Type: CONSTRAINT; Schema: daten; Owner: geoadmin
--

ALTER TABLE ONLY daten.tms_data
    ADD CONSTRAINT uq_tms_data_observation UNIQUE (deployment_id, height_id, sensor_name_id, datetime);


--
-- Name: tms_deployments uq_tms_deployments_id_serial; Type: CONSTRAINT; Schema: daten; Owner: geoadmin
--

ALTER TABLE ONLY daten.tms_deployments
    ADD CONSTRAINT uq_tms_deployments_id_serial UNIQUE (deployment_id, serial_number);


--
-- Name: tms_deployments uq_tms_deployments_serial_dates; Type: CONSTRAINT; Schema: daten; Owner: geoadmin
--

ALTER TABLE ONLY daten.tms_deployments
    ADD CONSTRAINT uq_tms_deployments_serial_dates UNIQUE (serial_number, start_date, collect_date);


--
-- Name: idx_tms_data_deployment_sensor_datetime; Type: INDEX; Schema: daten; Owner: geoadmin
--

CREATE INDEX idx_tms_data_deployment_sensor_datetime ON daten.tms_data USING btree (deployment_id, sensor_name_id, datetime) INCLUDE (value);


--
-- Name: idx_tms_data_serial_sensor_datetime; Type: INDEX; Schema: daten; Owner: geoadmin
--

CREATE INDEX idx_tms_data_serial_sensor_datetime ON daten.tms_data USING btree (serial_number, sensor_name_id, datetime) INCLUDE (value);


--
-- Name: tms_data trg_set_tms_deployment_id; Type: TRIGGER; Schema: daten; Owner: geoadmin
--

CREATE TRIGGER trg_set_tms_deployment_id BEFORE INSERT OR UPDATE OF serial_number, datetime ON daten.tms_data FOR EACH ROW EXECUTE FUNCTION daten.set_tms_deployment_id();


--
-- Name: tms_data fk_tms_data_deployment; Type: FK CONSTRAINT; Schema: daten; Owner: geoadmin
--

ALTER TABLE ONLY daten.tms_data
    ADD CONSTRAINT fk_tms_data_deployment FOREIGN KEY (deployment_id, serial_number) REFERENCES daten.tms_deployments(deployment_id, serial_number);


--
-- Name: tms_data fk_tms_data_height; Type: FK CONSTRAINT; Schema: daten; Owner: geoadmin
--

ALTER TABLE ONLY daten.tms_data
    ADD CONSTRAINT fk_tms_data_height FOREIGN KEY (height_id) REFERENCES daten.tms_lut_height(height_id);


--
-- Name: tms_data fk_tms_data_sensor; Type: FK CONSTRAINT; Schema: daten; Owner: geoadmin
--

ALTER TABLE ONLY daten.tms_data
    ADD CONSTRAINT fk_tms_data_sensor FOREIGN KEY (sensor_name_id) REFERENCES daten.tms_lut_sensor_name(sensor_name_id);


--
-- Name: tms_sensor_end fk_tms_sensor_end_deployment; Type: FK CONSTRAINT; Schema: daten; Owner: geoadmin
--

ALTER TABLE ONLY daten.tms_sensor_end
    ADD CONSTRAINT fk_tms_sensor_end_deployment FOREIGN KEY (deployment_id) REFERENCES daten.tms_deployments(deployment_id);


--
-- Name: tms_sensor_end fk_tms_sensor_end_sensor; Type: FK CONSTRAINT; Schema: daten; Owner: geoadmin
--

ALTER TABLE ONLY daten.tms_sensor_end
    ADD CONSTRAINT fk_tms_sensor_end_sensor FOREIGN KEY (sensor_name_id) REFERENCES daten.tms_lut_sensor_name(sensor_name_id);


--
-- Name: TABLE tms_data; Type: ACL; Schema: daten; Owner: geoadmin
--

REVOKE SELECT,INSERT,REFERENCES,DELETE,TRIGGER,TRUNCATE,UPDATE ON TABLE daten.tms_data FROM geoadmin;
GRANT SELECT,INSERT,REFERENCES,DELETE,TRIGGER,TRUNCATE,UPDATE ON TABLE daten.tms_data TO geoadmin WITH GRANT OPTION;
GRANT SELECT,INSERT,REFERENCES,DELETE,TRIGGER,TRUNCATE,UPDATE ON TABLE daten.tms_data TO fsadmin WITH GRANT OPTION;
GRANT SELECT ON TABLE daten.tms_data TO geouser;


--
-- Name: TABLE tms_deployments; Type: ACL; Schema: daten; Owner: geoadmin
--

REVOKE SELECT,INSERT,REFERENCES,DELETE,TRIGGER,TRUNCATE,UPDATE ON TABLE daten.tms_deployments FROM geoadmin;
GRANT SELECT,INSERT,REFERENCES,DELETE,TRIGGER,TRUNCATE,UPDATE ON TABLE daten.tms_deployments TO geoadmin WITH GRANT OPTION;
GRANT SELECT,INSERT,REFERENCES,DELETE,TRIGGER,TRUNCATE,UPDATE ON TABLE daten.tms_deployments TO fsadmin WITH GRANT OPTION;
GRANT SELECT ON TABLE daten.tms_deployments TO geouser;


--
-- Name: TABLE tms_sensor_end; Type: ACL; Schema: daten; Owner: geoadmin
--

REVOKE SELECT,INSERT,REFERENCES,DELETE,TRIGGER,TRUNCATE,UPDATE ON TABLE daten.tms_sensor_end FROM geoadmin;
GRANT SELECT,INSERT,REFERENCES,DELETE,TRIGGER,TRUNCATE,UPDATE ON TABLE daten.tms_sensor_end TO geoadmin WITH GRANT OPTION;
GRANT SELECT,INSERT,REFERENCES,DELETE,TRIGGER,TRUNCATE,UPDATE ON TABLE daten.tms_sensor_end TO fsadmin WITH GRANT OPTION;
GRANT SELECT ON TABLE daten.tms_sensor_end TO geouser;


--
-- Name: TABLE tms_data_valid; Type: ACL; Schema: daten; Owner: geoadmin
--

REVOKE SELECT,INSERT,REFERENCES,DELETE,TRIGGER,TRUNCATE,UPDATE ON TABLE daten.tms_data_valid FROM geoadmin;
GRANT SELECT,INSERT,REFERENCES,DELETE,TRIGGER,TRUNCATE,UPDATE ON TABLE daten.tms_data_valid TO geoadmin WITH GRANT OPTION;
GRANT SELECT,INSERT,REFERENCES,DELETE,TRIGGER,TRUNCATE,UPDATE ON TABLE daten.tms_data_valid TO fsadmin WITH GRANT OPTION;
GRANT SELECT ON TABLE daten.tms_data_valid TO geouser;


--
-- Name: TABLE tms_lut_height; Type: ACL; Schema: daten; Owner: geoadmin
--

REVOKE SELECT,INSERT,REFERENCES,DELETE,TRIGGER,TRUNCATE,UPDATE ON TABLE daten.tms_lut_height FROM geoadmin;
GRANT SELECT,INSERT,REFERENCES,DELETE,TRIGGER,TRUNCATE,UPDATE ON TABLE daten.tms_lut_height TO geoadmin WITH GRANT OPTION;
GRANT SELECT,INSERT,REFERENCES,DELETE,TRIGGER,TRUNCATE,UPDATE ON TABLE daten.tms_lut_height TO fsadmin WITH GRANT OPTION;
GRANT SELECT ON TABLE daten.tms_lut_height TO geouser;


--
-- Name: TABLE tms_lut_sensor_name; Type: ACL; Schema: daten; Owner: geoadmin
--

REVOKE SELECT,INSERT,REFERENCES,DELETE,TRIGGER,TRUNCATE,UPDATE ON TABLE daten.tms_lut_sensor_name FROM geoadmin;
GRANT SELECT,INSERT,REFERENCES,DELETE,TRIGGER,TRUNCATE,UPDATE ON TABLE daten.tms_lut_sensor_name TO geoadmin WITH GRANT OPTION;
GRANT SELECT,INSERT,REFERENCES,DELETE,TRIGGER,TRUNCATE,UPDATE ON TABLE daten.tms_lut_sensor_name TO fsadmin WITH GRANT OPTION;
GRANT SELECT ON TABLE daten.tms_lut_sensor_name TO geouser;


--
-- PostgreSQL database dump complete
--

--
-- PostgreSQL database dump
--

-- Dumped from database version 12.22 (Ubuntu 12.22-0ubuntu0.20.04.4)
-- Dumped by pg_dump version 17.5 (Ubuntu 17.5-1.pgdg20.04+1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: externe_daten; Type: SCHEMA; Schema: -; Owner: geoadmin
--

CREATE SCHEMA externe_daten;


ALTER SCHEMA externe_daten OWNER TO geoadmin;

--
-- Name: SCHEMA externe_daten; Type: COMMENT; Schema: -; Owner: geoadmin
--

COMMENT ON SCHEMA externe_daten IS 'Daten externer Dienste (z. B. ERA5 via Open-Meteo); befüllt per ETL';


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: met_data; Type: TABLE; Schema: externe_daten; Owner: geoadmin
--

CREATE TABLE externe_daten.met_data (
    met_point_id integer NOT NULL,
    variable text NOT NULL,
    datetime timestamp with time zone NOT NULL,
    value double precision,
    CONSTRAINT chk_met_data_variable CHECK ((variable = ANY (ARRAY['t2m'::text, 'precip'::text, 'ghi'::text])))
);


ALTER TABLE externe_daten.met_data OWNER TO geoadmin;

--
-- Name: met_point_verjkr; Type: TABLE; Schema: externe_daten; Owner: geoadmin
--

CREATE TABLE externe_daten.met_point_verjkr (
    verjkr_id character varying(9) NOT NULL,
    met_point_id integer NOT NULL
);


ALTER TABLE externe_daten.met_point_verjkr OWNER TO geoadmin;

--
-- Name: met_points; Type: TABLE; Schema: externe_daten; Owner: geoadmin
--

CREATE TABLE externe_daten.met_points (
    met_point_id integer NOT NULL,
    source text NOT NULL,
    ext_id text NOT NULL,
    lat double precision NOT NULL,
    lon double precision NOT NULL,
    grid_lat double precision,
    grid_lon double precision,
    elevation double precision
);


ALTER TABLE externe_daten.met_points OWNER TO geoadmin;

--
-- Name: met_points_met_point_id_seq; Type: SEQUENCE; Schema: externe_daten; Owner: geoadmin
--

ALTER TABLE externe_daten.met_points ALTER COLUMN met_point_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME externe_daten.met_points_met_point_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: met_data pk_met_data; Type: CONSTRAINT; Schema: externe_daten; Owner: geoadmin
--

ALTER TABLE ONLY externe_daten.met_data
    ADD CONSTRAINT pk_met_data PRIMARY KEY (met_point_id, variable, datetime);


--
-- Name: met_point_verjkr pk_met_point_verjkr; Type: CONSTRAINT; Schema: externe_daten; Owner: geoadmin
--

ALTER TABLE ONLY externe_daten.met_point_verjkr
    ADD CONSTRAINT pk_met_point_verjkr PRIMARY KEY (verjkr_id, met_point_id);


--
-- Name: met_points pk_met_points; Type: CONSTRAINT; Schema: externe_daten; Owner: geoadmin
--

ALTER TABLE ONLY externe_daten.met_points
    ADD CONSTRAINT pk_met_points PRIMARY KEY (met_point_id);


--
-- Name: met_points uq_met_points_source_ext; Type: CONSTRAINT; Schema: externe_daten; Owner: geoadmin
--

ALTER TABLE ONLY externe_daten.met_points
    ADD CONSTRAINT uq_met_points_source_ext UNIQUE (source, ext_id);


--
-- Name: met_data fk_met_data_point; Type: FK CONSTRAINT; Schema: externe_daten; Owner: geoadmin
--

ALTER TABLE ONLY externe_daten.met_data
    ADD CONSTRAINT fk_met_data_point FOREIGN KEY (met_point_id) REFERENCES externe_daten.met_points(met_point_id);


--
-- Name: met_point_verjkr fk_met_point_verjkr_point; Type: FK CONSTRAINT; Schema: externe_daten; Owner: geoadmin
--

ALTER TABLE ONLY externe_daten.met_point_verjkr
    ADD CONSTRAINT fk_met_point_verjkr_point FOREIGN KEY (met_point_id) REFERENCES externe_daten.met_points(met_point_id);


--
-- Name: met_point_verjkr fk_met_point_verjkr_verjkr; Type: FK CONSTRAINT; Schema: externe_daten; Owner: geoadmin
--

ALTER TABLE ONLY externe_daten.met_point_verjkr
    ADD CONSTRAINT fk_met_point_verjkr_verjkr FOREIGN KEY (verjkr_id) REFERENCES plots.be_verjuengungskreise_pt(verjkr_id);


--
-- Name: SCHEMA externe_daten; Type: ACL; Schema: -; Owner: geoadmin
--

GRANT USAGE ON SCHEMA externe_daten TO fsadmin;
GRANT USAGE ON SCHEMA externe_daten TO geouser;


--
-- Name: TABLE met_data; Type: ACL; Schema: externe_daten; Owner: geoadmin
--

REVOKE SELECT,INSERT,REFERENCES,DELETE,TRIGGER,TRUNCATE,UPDATE ON TABLE externe_daten.met_data FROM geoadmin;
GRANT SELECT,INSERT,REFERENCES,DELETE,TRIGGER,TRUNCATE,UPDATE ON TABLE externe_daten.met_data TO geoadmin WITH GRANT OPTION;
GRANT SELECT,INSERT,REFERENCES,DELETE,TRIGGER,TRUNCATE,UPDATE ON TABLE externe_daten.met_data TO fsadmin WITH GRANT OPTION;
GRANT SELECT ON TABLE externe_daten.met_data TO geouser;


--
-- Name: TABLE met_point_verjkr; Type: ACL; Schema: externe_daten; Owner: geoadmin
--

REVOKE SELECT,INSERT,REFERENCES,DELETE,TRIGGER,TRUNCATE,UPDATE ON TABLE externe_daten.met_point_verjkr FROM geoadmin;
GRANT SELECT,INSERT,REFERENCES,DELETE,TRIGGER,TRUNCATE,UPDATE ON TABLE externe_daten.met_point_verjkr TO geoadmin WITH GRANT OPTION;
GRANT SELECT,INSERT,REFERENCES,DELETE,TRIGGER,TRUNCATE,UPDATE ON TABLE externe_daten.met_point_verjkr TO fsadmin WITH GRANT OPTION;
GRANT SELECT ON TABLE externe_daten.met_point_verjkr TO geouser;


--
-- Name: TABLE met_points; Type: ACL; Schema: externe_daten; Owner: geoadmin
--

REVOKE SELECT,INSERT,REFERENCES,DELETE,TRIGGER,TRUNCATE,UPDATE ON TABLE externe_daten.met_points FROM geoadmin;
GRANT SELECT,INSERT,REFERENCES,DELETE,TRIGGER,TRUNCATE,UPDATE ON TABLE externe_daten.met_points TO geoadmin WITH GRANT OPTION;
GRANT SELECT,INSERT,REFERENCES,DELETE,TRIGGER,TRUNCATE,UPDATE ON TABLE externe_daten.met_points TO fsadmin WITH GRANT OPTION;
GRANT SELECT ON TABLE externe_daten.met_points TO geouser;


--
-- Name: SEQUENCE met_points_met_point_id_seq; Type: ACL; Schema: externe_daten; Owner: geoadmin
--

GRANT SELECT,USAGE ON SEQUENCE externe_daten.met_points_met_point_id_seq TO fsadmin;


--
-- Name: DEFAULT PRIVILEGES FOR SEQUENCES; Type: DEFAULT ACL; Schema: externe_daten; Owner: geoadmin
--

ALTER DEFAULT PRIVILEGES FOR ROLE geoadmin IN SCHEMA externe_daten GRANT SELECT,USAGE ON SEQUENCES TO fsadmin;


--
-- Name: DEFAULT PRIVILEGES FOR TABLES; Type: DEFAULT ACL; Schema: externe_daten; Owner: geoadmin
--

ALTER DEFAULT PRIVILEGES FOR ROLE geoadmin IN SCHEMA externe_daten GRANT SELECT,INSERT,REFERENCES,DELETE,TRIGGER,TRUNCATE,UPDATE ON TABLES TO fsadmin WITH GRANT OPTION;
ALTER DEFAULT PRIVILEGES FOR ROLE geoadmin IN SCHEMA externe_daten GRANT SELECT ON TABLES TO geouser;


--
-- PostgreSQL database dump complete
--

